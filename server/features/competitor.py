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


@dataclass
class CompetitorAnalysisOutcome:
    considered: int = 0
    analysed: int = 0
    released: int = 0
    failed: int = 0
    features_written: int = 0
    compute_seconds: float = 0.0
    by_stage: dict = field(default_factory=dict)
    errors: list[dict] = field(default_factory=list)
    audit: dict = field(default_factory=dict)


def _stored_feature_count(session: Session, video_id: int,
                          method_versions: list[str]) -> int:
    return session.scalar(
        select(func.count(VideoFeature.id)).where(
            VideoFeature.video_id == video_id,
            VideoFeature.method_version.in_(method_versions))) or 0


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

    versions = [mv for _, _, mv in STAGES]
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

    if live:
        paths = {v.id: Path(r.path) for r, v in live}
        videos = [v for _, v in live]
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

    # Confirm before deleting. "The extractor ran" is a different claim
    # from "the evidence is stored", and only the second justifies
    # removing the only copy of the input.
    for row, video in live:
        stored = _stored_feature_count(session, video.id, versions)
        if stored == 0:
            out.failed += 1
            out.errors.append({
                "video_id": video.id,
                "error": ("no features were stored; the file is KEPT so "
                          "the extraction can be retried before its TTL")})
            continue
        out.analysed += 1
        if transient.release(session, row, reason="features_persisted",
                             now=stamp):
            out.released += 1

    session.flush()
    out.compute_seconds = round(time.monotonic() - started, 2)
    out.audit = transient.audit(session, now=stamp)
    logger.info("competitor analysis: %d analysed, %d released, %d failed, "
                "%d features in %.1fs", out.analysed, out.released,
                out.failed, out.features_written, out.compute_seconds)
    return out


def coverage(session: Session) -> dict:
    """How much competitor content is actually analysed.

    M2's acceptance criterion counts publications *and* creators, so both
    are reported. The note is not decoration: twenty publications spread
    one-per-creator satisfies the stated criterion and still cannot
    support a creator baseline, which is what every M5/M6 comparison is
    defined against.
    """
    from server.db.models import Creator

    rows = session.execute(
        select(Video.id, Video.creator_id)
        .join(Creator, Creator.id == Video.creator_id)
        .join(VideoFeature, VideoFeature.video_id == Video.id)
        .where(Creator.is_own_account.is_(False),
               VideoFeature.method_version == hookfeat.METHOD_VERSION)
        .distinct()).all()

    publications = {vid for vid, _ in rows}
    creators = {cid for _, cid in rows}
    per_creator: dict[int, int] = {}
    for _, cid in rows:
        per_creator[cid] = per_creator.get(cid, 0) + 1
    deep = sum(1 for n in per_creator.values() if n >= 20)

    return {
        "competitor_publications_analysed": len(publications),
        "competitor_creators_analysed": len(creators),
        "creators_with_20_or_more": deep,
        "meets_m2_acceptance_as_written": (len(publications) >= 20
                                           and len(creators) >= 10),
        "meets_the_stricter_reading": deep >= 10,
        "note": ("M2 as written asks for >=20 publications across >=10 "
                 "creators, which one publication each from twenty "
                 "creators would satisfy. `creators_with_20_or_more` is "
                 "the figure that actually supports a per-creator "
                 "baseline, and it is the one to watch."),
        "method_version": hookfeat.METHOD_VERSION,
    }
