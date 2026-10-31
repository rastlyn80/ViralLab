"""Measure a video you have not posted yet, against what works.

The owner's request: "the creative suite also should analyze the
videos that I create before posting them and suggest anything that
could make them better."

Everything else in this project looks backwards. This is the only
surface that looks at a video while it can still be changed, which
makes it the one that has to be most careful about what it claims.

## It is never a score

BLUEPRINT M10 names this capability and writes the constraint into
its own row: *"a checklist of Validated and Associated features
present in a draft, **never a score**"*. A number out of ten would
be read as a probability of success, and nothing here can support
that. So the output is a list of checks, each naming the evidence
behind it and how strong that evidence is.

## What it checks against

Only measurements that may support a claim:

* **Replicated** - held on his account AND independently on other
  creators. The strongest thing this project produces, and the
  only group whose advice is worth following without hesitation.
* **His account only** - real, and possibly a description of him
  rather than of piano videos.
* Everything else is measured and shown without a verdict.

## The draft never enters the corpus

Nothing here writes a `videos` row. A draft in the database would
be a video with no views, and the analysis scores videos against
their creator's view distribution - so one unposted draft would
sit at the bottom of every ranking and quietly drag the findings
it is being judged against. The file is measured in place and the
numbers are returned, not stored.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from server.features import lexicon as lex

logger = logging.getLogger("virallab.ai.prepublish")

#: What a check can say.
GOOD = "matches"
AGAINST = "goes against"
UNKNOWN = "not measured"


@dataclass
class Check:
    what: str
    verdict: str
    detail: str
    evidence: str
    yours: float | None = None
    target: float | None = None

    def as_dict(self) -> dict:
        return {"what": self.what, "verdict": self.verdict,
                "detail": self.detail, "evidence": self.evidence,
                "yours": self.yours, "target": self.target}


@dataclass
class Draft:
    """Everything measured about one unposted video."""

    measured: dict = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)
    could_not_measure: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        order = {AGAINST: 0, GOOD: 1, UNKNOWN: 2}
        checks = sorted(self.checks,
                        key=lambda c: (order.get(c.verdict, 3), c.what))
        return {
            "checks": [c.as_dict() for c in checks],
            "summary": {
                "goes_against": sum(1 for c in self.checks
                                    if c.verdict == AGAINST),
                "matches": sum(1 for c in self.checks
                               if c.verdict == GOOD),
                "not_measured": len(self.could_not_measure),
            },
            "measured": {k: round(v, 4) if isinstance(v, float) else v
                         for k, v in self.measured.items()},
            "could_not_measure": self.could_not_measure,
            "notes": self.notes,
            "never_a_score": (
                "There is no total and no mark out of ten. A number "
                "here would be read as a chance of success, and "
                "nothing measured can support that. Each line says "
                "what it rests on."),
        }


def measure_file(path: Path) -> tuple[dict, list[str]]:
    """Run the same extractors a published video goes through.

    No database, no `videos` row. A draft with no views would sit
    at the bottom of every within-creator ranking and drag the
    findings it is being judged against.
    """
    from shared import hookbg, hookfeat

    out: dict = {}
    missed: list[str] = []
    # Each stage returns an object carrying `.measurements`, the same
    # dict `extract.py` stores for a published video. Calling the
    # shared functions rather than the DB-writing runners is what
    # keeps a draft out of the corpus.
    try:
        out.update(hookfeat.extract_hook(path).measurements or {})
    except Exception as exc:                # noqa: BLE001 - reported
        logger.warning("deterministic pass failed on a draft: %s", exc)
        missed.append("the opening's light, movement and sound")
    try:
        out.update(hookbg.extract_background(path).measurements or {})
    except Exception as exc:                # noqa: BLE001 - reported
        logger.warning("background pass failed on a draft: %s", exc)
        missed.append("how busy the background is")
    try:
        out.update(_read_text(path))
    except Exception as exc:                # noqa: BLE001 - reported
        logger.warning("ocr pass failed on a draft: %s", exc)
        missed.append("the words on screen")
    return out, missed


def _read_text(path: Path) -> dict:
    """On-screen text in the opening, via the same OCR path.

    The frame geometry is copied from `extract.run_ocr` rather than
    chosen here: OCR reads a squashed frame worse, and two callers
    decoding differently would measure the same video differently.
    """
    from shared import hookfeat, hookocr

    info = hookfeat.probe(path)
    width = hookocr.OCR_WIDTH
    src_w = int(info.get("width") or width)
    src_h = int(info.get("height") or width)
    height = max(2, int(round(width * src_h / max(1, src_w))) // 2 * 2)
    frames = hookfeat.decode_frames(
        path, start=0.0, seconds=hookfeat.HOOK_SECONDS,
        fps=hookocr.OCR_FPS, width=width, height=height)
    if frames.shape[0] == 0:
        raise hookfeat.ExtractionError("no frames decoded")
    stamps = [i / hookocr.OCR_FPS for i in range(frames.shape[0])]
    result = hookocr.analyse_frames(
        frames, timestamps=stamps, hook_seconds=hookfeat.HOOK_SECONDS)
    return dict(hookocr.measurements(
        result, (0.0, hookfeat.HOOK_SECONDS)) or {})


def from_caption(caption: str) -> dict:
    """The caption measurements, from text the owner typed.

    A draft has no caption in the database - he is still writing
    it - so the same extractor is run over what he pastes in.
    """
    from datetime import datetime, timezone

    from server.features import publication_meta as pm

    try:
        # `posted_at` is now: a draft has no publication time, and
        # the timing measurements are returned so he can see what
        # posting now would look like, not treated as a decision
        # already taken.
        return dict(pm.extract(caption=caption or "",
                               posted_at=datetime.now(timezone.utc),
                               duration_s=None) or {})
    except Exception:                       # noqa: BLE001 - reported
        logger.exception("caption pass failed on a draft")
        return {}


def _targets(session: Session) -> list[dict]:
    """Every measurement a draft can be judged against.

    Replicated ones first and labelled as such: they held on his
    account and independently on other creators, which is as strong
    as this project gets.
    """
    from server.research import virality as vir

    mine = vir.breakthroughs(session, own=True)
    gradient = vir.build(session, own=True)
    theirs = vir.build(session, own=False)
    their_breaks = vir.breakthroughs(session, own=False)

    # Same direction, not just the same name. A measurement that
    # goes one way for him and the other way for everyone else is
    # a contradiction, and labelling it "held on your account and
    # on other creators" is the strongest claim in the product
    # attached to its weakest evidence.
    their_all = (list(theirs.get("findings", []))
                 + list(their_breaks.get("differences", [])))
    mine_all = (list(mine.get("differences", []))
                + list(gradient.get("findings", [])))
    agreed, _contradicts = vir.directional_overlap(mine_all, their_all)
    their_names = set(agreed)

    out: list[dict] = []
    for d in mine.get("differences", []):
        replicated = d["display_name"] in their_names
        out.append({
            "feature": d["feature"],
            "display_name": d["display_name"],
            "direction": d["direction"],
            "threshold": (d["outliers_mean"] + d["others_mean"]) / 2.0,
            "breakout_level": d["outliers_mean"],
            "is_boolean": d.get("is_boolean", False),
            "evidence": ("held on your account and on other creators"
                         if replicated else "held on your account"),
            "replicated": replicated,
        })
    seen = {d["feature"] for d in out}
    for f in gradient.get("findings", []):
        if f["feature"] in seen:
            continue
        if f["display_name"] not in their_names:
            continue        # his account alone, gradient only: too weak
        out.append({
            "feature": f["feature"],
            "display_name": f["display_name"],
            "direction": ("higher" if f["direction"] == "positive"
                          else "lower"),
            "threshold": None, "breakout_level": None,
            "is_boolean": False,
            "evidence": "held on your account and on other creators",
            "replicated": True,
        })
    out.sort(key=lambda d: not d["replicated"])
    return out


def check(session: Session, *, media_path: Path | None = None,
          caption: str = "") -> Draft:
    """Measure a draft and say where it stands."""
    draft = Draft()
    values: dict = {}

    if media_path is not None:
        measured, missed = measure_file(media_path)
        values.update(measured)
        draft.could_not_measure.extend(missed)
    else:
        draft.could_not_measure.append(
            "anything about the video itself - no file was given")
    if caption:
        values.update(from_caption(caption))
    else:
        draft.could_not_measure.append(
            "anything about the caption - none was written yet")
    draft.measured = values

    for target in _targets(session):
        value = values.get(target["feature"])
        name = target["display_name"]
        if value is None:
            continue
        number = float(value)
        if target["threshold"] is None:
            # A gradient finding has no level to clear, only a
            # direction. Reported, never graded: "more is better"
            # cannot say whether THIS draft has enough.
            draft.checks.append(Check(
                what=name, verdict=UNKNOWN,
                detail=(f"{'More' if target['direction'] == 'higher' else 'Less'} "
                        f"of this went with doing better. Yours is "
                        f"{number:.3g}, and there is no level to clear - "
                        f"only a direction."),
                evidence=target["evidence"], yours=number))
            continue
        wants_higher = target["direction"] == "higher"
        ok = number >= target["threshold"] if wants_higher \
            else number <= target["threshold"]
        if target["is_boolean"]:
            detail = (f"Your breakthroughs had this on "
                      f"{target['breakout_level']:.0%} of them. "
                      f"This draft: {'yes' if number >= 0.5 else 'no'}.")
        else:
            detail = (f"Yours is {number:.3g}; the line is "
                      f"{target['threshold']:.3g} and your "
                      f"breakthroughs averaged "
                      f"{target['breakout_level']:.3g}.")
        draft.checks.append(Check(
            what=name, verdict=GOOD if ok else AGAINST,
            detail=detail, evidence=target["evidence"],
            yours=number, target=round(target["threshold"], 4)))

    if not draft.checks:
        draft.notes.append(
            "Nothing could be checked. Either the file could not be "
            "measured or none of the measurements that have evidence "
            "behind them apply to it.")
    draft.notes.append(
        "A draft is measured in place and nothing is written down. "
        "It never joins the videos the findings are computed from.")
    return draft
