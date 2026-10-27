"""Analyse competitor media, then delete it. The second half of M2.

`server/collectors/competitor_media.py` brings the bytes in and
`server/collectors/transient.py` holds the deadline. This closes the
loop: derive the features, confirm they are stored, and only then
release the file.

## The ordering is the point

CLAUDE.md §4.7 — the worker deletes its copy *once the server confirms
the result was persisted*. The same rule applies here, in the same
direction, for a different reason: deleting first would lose the only
copy of an analysis that then failed, and deleting on a timer while
extraction is mid-flight would do it unpredictably. So:

1. extract, which commits each video's features as it goes;
2. re-read the stored features to confirm they are really there;
3. release the file with ``features_persisted``.

Step 2 is not ceremony. `_store` can write zero rows for a video whose
extractor returned nothing, and "the extractor ran" is not the same
claim as "the evidence exists". Releasing on the first without checking
the second is how a file gets deleted in exchange for nothing.

**Confirmation is per stage, against that stage's own method version.**
An earlier version counted feature rows across all three method
versions at once and released the file as soon as the total was
non-zero. That is a real failure mode, not a hypothetical: a
publication whose deterministic pass succeeded and whose background and
OCR passes both failed counted as fully analysed, lost its only copy of
the input, and recorded nothing to say two thirds of the work was
missing. The retry it needed was impossible because the bytes were
gone.

So each publication is classified, and the classification decides what
happens to the file:

* ``complete`` — every stage in `STAGES` has at least one row under its
  own method version. Only this releases the file.
* ``partial`` — some stages stored rows and some did not. The features
  that did land are **kept**; the file is kept too, so the missing
  stages can be retried inside the TTL. Nothing claims the publication
  is analysed.
* ``failed`` — no stage stored anything. File kept for retry.
* ``expired`` — the six-hour deadline passed before the work finished.
  The reaper owns the file from that point; whatever features exist are
  kept and the publication is reported as incomplete, because it is.

`partial` and `failed` never shorten the deadline and never extend it.
The TTL is absolute: keeping a file for a retry means keeping it until
`expires_at`, not past it.

## What is NOT done here

No vision. M3 exists because the local VLM returned a lift of −0.020
over a constant answerer, and sending competitor frames to a cloud
provider needs both a `cloud_vision` cap (currently $0.00) and a
provider whose terms permit third-party frames — recorded, not assumed.
The CPU extractors have no such question hanging over them: they read
pixels and produce numbers, on the same machine the file already sits
on.

No speech either. Phase 0B measured Whisper hallucinating on music-only
audio and real speech in 1–2 of 14 publications in this niche
(CLAUDE.md §5.9). Running it over competitor music would mostly
manufacture text.

So competitor publications get exactly what own publications get from
the CPU path: `hook-deterministic-v1`, `hook-background-v1`,
`hook-ocr-v1`. Same method versions, same `video_features` table, same
comparability.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.collectors import transient
from server.db.models import TransientMedia, Video, VideoFeature
from server.features import extract
from shared import hookbg, hookfeat, hookocr

logger = logging.getLogger("virallab.features.competitor")

#: The CPU extractors, in the order they run. Each is (name, runner,
#: method_version). Vision and speech are deliberately absent — see the
#: module docstring.
STAGES = (
    ("deterministic", extract.run_deterministic, hookfeat.METHOD_VERSION),
    ("background", extract.run_background, hookbg.METHOD_VERSION),
    ("ocr", extract.run_ocr, hookocr.METHOD_VERSION),
)


#: The four states a held publication can end a run in. `complete` is
#: the only one that releases the file.
OUTCOME_STATES = ("complete", "partial", "failed", "expired")


@dataclass
class CompetitorAnalysisOutcome:
    considered: int = 0
    #: Every stage stored rows under its own method version.
    complete: int = 0
    #: Some stages stored rows, some did not. Features kept, file kept.
    partial: int = 0
    #: No stage stored anything.
    failed: int = 0
    #: The TTL passed before the work finished; the reaper owns the file.
    expired: int = 0
    released: int = 0
    features_written: int = 0
    compute_seconds: float = 0.0
    by_stage: dict = field(default_factory=dict)
    #: Per publication: {video_id: {"state", "stages_done", "stages_missing"}}
    by_video: dict = field(default_factory=dict)
    errors: list[dict] = field(default_factory=list)
    audit: dict = field(default_factory=dict)

    @property
    def fully_analysed(self) -> int:
        """The only count that may be reported as "analysed".

        Named so that reaching for it is a deliberate act. `complete` is
        the same number; this exists because "analysed" was the word the
        old field used for complete-or-partial, and the ambiguity is
        what caused the bug.
        """
        return self.complete


def _rows_for_stage(session: Session, video_id: int,
                    method_version: str) -> int:
    """Stored feature rows for ONE stage, under ITS OWN method version.

    The `method_version` filter is the point. Counting across all three
    at once is what let one successful stage stand in for three.
    """
    return session.scalar(
        select(func.count(VideoFeature.id)).where(
            VideoFeature.video_id == video_id,
            VideoFeature.method_version == method_version)) or 0


def completion(session: Session, video_id: int, *,
               expired: bool = False) -> dict:
    """Which extraction stages actually produced stored evidence.

    Returns the per-stage row counts, the stages still missing, and the
    state those imply. `expired` is passed in rather than derived here
    because it is a property of the file's deadline, not of the
    features, and this function deliberately knows nothing about
    deadlines.
    """
    counts = {name: _rows_for_stage(session, video_id, mv)
              for name, _runner, mv in STAGES}
    done = sorted(n for n, c in counts.items() if c > 0)
    missing = sorted(n for n, c in counts.items() if c == 0)

    if not done:
        state = "failed"
    elif missing:
        state = "partial"
    else:
        state = "complete"
    # An expired file cannot be retried, so a run that did not finish
    # before the deadline is reported as expired whatever it managed.
    # A publication that DID complete before expiry is complete: the
    # evidence is stored and the deadline is now irrelevant to it.
    if expired and state != "complete":
        state = "expired"

    return {
        "state": state,
        "rows_by_stage": counts,
        "stages_done": done,
        "stages_missing": missing,
        "method_versions": {name: mv for name, _r, mv in STAGES},
    }


def analyse_held(session: Session, *, media_root: Path | None = None,
                 limit: int = 50,
                 now: datetime | None = None
                 ) -> CompetitorAnalysisOutcome:
    """Analyse every competitor file currently in quarantine, then delete.

    Safe to re-run: a publication whose features already exist under the
    current method versions is released without being re-extracted, and
    a file that has already gone leaves its row reconciled by the sweep.
    """
    import time

    stamp = now or datetime.now(timezone.utc)
    out = CompetitorAnalysisOutcome()
    started = time.monotonic()

    rows: list[TransientMedia] = list(session.scalars(
        select(TransientMedia)
        .where(TransientMedia.deleted_at.is_(None))
        .order_by(TransientMedia.acquired_at)
        .limit(limit)).all())
    out.considered = len(rows)
    if not rows:
        out.audit = transient.audit(session, now=stamp)
        return out

    live: list[tuple[TransientMedia, Video]] = []
    for row in rows:
        video = session.get(Video, row.video_id)
        if video is None:
            out.errors.append({"transient_id": row.id,
                               "error": "publication row is gone"})
            transient.release(session, row, reason="cancelled", now=stamp)
            continue
        if not Path(row.path).exists():
            # The sweep will reconcile it; nothing to analyse.
            out.errors.append({"video_id": video.id,
                               "error": "file already absent"})
            continue
        live.append((row, video))

    # Already-complete publications are released without re-extracting.
    # Checking first is what makes the "safe to re-run" claim true
    # rather than merely cheap.
    todo: list[tuple[TransientMedia, Video]] = []
    for row, video in live:
        before = completion(session, video.id,
                            expired=row.expires_at <= stamp)
        if before["state"] == "complete":
            out.by_video[video.id] = {**before, "re_extracted": False}
            out.complete += 1
            if transient.release(session, row, reason="features_persisted",
                                 now=stamp):
                out.released += 1
        else:
            todo.append((row, video))

    if todo:
        paths = {v.id: Path(r.path) for r, v in todo}
        videos = [v for _, v in todo]
        for name, runner, _mv in STAGES:
            result = runner(session, videos, media_root=media_root,
                            paths=paths)
            out.by_stage[name] = {
                "done": result.done, "failed": result.failed,
                "features": result.features_written,
                "seconds": result.compute_seconds}
            out.features_written += result.features_written
            for err in result.errors:
                out.errors.append({"stage": name, **err})

    # Confirm before deleting, per stage. "The extractor ran" is a
    # different claim from "the evidence is stored", and "one stage
    # stored something" is a different claim again from "the work is
    # done". Only the last one justifies removing the only copy of the
    # input.
    for row, video in todo:
        state = completion(session, video.id,
                           expired=row.expires_at <= stamp)
        out.by_video[video.id] = {**state, "re_extracted": True}

        if state["state"] == "complete":
            out.complete += 1
            if transient.release(session, row, reason="features_persisted",
                                 now=stamp):
                out.released += 1
            continue

        # Everything below keeps the file. Features already stored are
        # kept too - a partial result is evidence, and discarding it
        # would mean re-paying for work that succeeded.
        out.errors.append({
            "video_id": video.id,
            "state": state["state"],
            "stages_done": state["stages_done"],
            "stages_missing": state["stages_missing"],
            "error": (
                f"{state['state']}: {len(state['stages_missing'])} of "
                f"{len(STAGES)} stage(s) stored nothing "
                f"({', '.join(state['stages_missing'])}). "
                + ("the deadline has passed, so the reaper will delete "
                   "the file and this publication stays incomplete"
                   if state["state"] == "expired"
                   else "the file is KEPT so the missing stages can be "
                        "retried before its TTL")),
        })
        if state["state"] == "expired":
            out.expired += 1
        elif state["state"] == "partial":
            out.partial += 1
        else:
            out.failed += 1

    session.flush()
    out.compute_seconds = round(time.monotonic() - started, 2)
    out.audit = transient.audit(session, now=stamp)
    logger.info("competitor analysis: %d complete, %d partial, %d failed, "
                "%d expired, %d released, %d features in %.1fs",
                out.complete, out.partial, out.failed, out.expired,
                out.released, out.features_written, out.compute_seconds)
    return out


def coverage(session: Session) -> dict:
    """How much competitor content is actually analysed.

    M2's acceptance criterion counts publications *and* creators, so both
    are reported. The note is not decoration: twenty publications spread
    one-per-creator satisfies the stated criterion and still cannot
    support a creator baseline, which is what every M5/M6 comparison is
    defined against.

    **"Analysed" means every stage, not any stage.** This used to count
    publications having rows under `hookfeat.METHOD_VERSION` alone, so a
    publication whose deterministic pass worked and whose background and
    OCR passes failed was indistinguishable from one that finished. The
    complete and partial counts are now separate, and only the complete
    one is checked against the acceptance criterion.
    """
    from server.db.models import Creator

    rows = session.execute(
        select(Video.id, Video.creator_id, VideoFeature.method_version)
        .join(Creator, Creator.id == Video.creator_id)
        .join(VideoFeature, VideoFeature.video_id == Video.id)
        .where(Creator.is_own_account.is_(False),
               VideoFeature.method_version.in_(
                   [mv for _n, _r, mv in STAGES]))
        .distinct()).all()

    wanted = {mv for _n, _r, mv in STAGES}
    seen: dict[int, set[str]] = {}
    creator_of: dict[int, int] = {}
    for vid, cid, mv in rows:
        seen.setdefault(vid, set()).add(mv)
        creator_of[vid] = cid

    complete = {vid for vid, mvs in seen.items() if mvs >= wanted}
    partial = {vid for vid, mvs in seen.items() if mvs and not mvs >= wanted}

    creators = {creator_of[v] for v in complete}
    per_creator: dict[int, int] = {}
    for v in complete:
        cid = creator_of[v]
        per_creator[cid] = per_creator.get(cid, 0) + 1
    deep = sum(1 for n in per_creator.values() if n >= 20)

    return {
        "competitor_publications_analysed": len(complete),
        "competitor_publications_partial": len(partial),
        "competitor_creators_analysed": len(creators),
        "creators_with_20_or_more": deep,
        "meets_m2_acceptance_as_written": (len(complete) >= 20
                                           and len(creators) >= 10),
        "meets_the_stricter_reading": deep >= 10,
        "note": ("M2 as written asks for >=20 publications across >=10 "
                 "creators, which one publication each from twenty "
                 "creators would satisfy. `creators_with_20_or_more` is "
                 "the figure that actually supports a per-creator "
                 "baseline, and it is the one to watch."),
        "counted_as_analysed_only_if": sorted(wanted),
        "partial_note": ("`..._partial` have SOME stages stored and are "
                         "excluded from every count above. Their stored "
                         "features are kept and remain usable on their "
                         "own terms; what they are not is finished."),
    }
