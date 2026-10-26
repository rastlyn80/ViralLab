"""Score the vision model against human reference labels.

Two label passes exist and the distinction matters.

`human_labels.json` was written from a single frame per video (t=1.0s)
*before* any inference ran. That ordering is the right one, but the labels
were not comparable with the model's view: the model is shown four hook
frames, and 47 disagreements were simply unresolvable - the model said
true, the one labelled frame said false, and nothing decided which was
right.

`human_labels_v2.json` resolves them by reviewing the full frame strips,
labelling "visible in any of t=0,1,2,3". That window is not a choice made
here: `vision_task.PROMPT` tells the model the 5s and 12s frames exist only
to judge whether the opening is representative, so scoring presence on them
would charge the model for following its instructions. The second pass was
necessarily written after inference, which is a weaker guarantee than the
first pass had, and the file records that plainly.

**Accuracy alone is close to meaningless on this corpus.** Every presence
boolean is heavily skewed - a performer is visible in all 24 openings, a
piano in 21. A model answering `true` unconditionally scores 0.875 on
piano. Every field is therefore reported against its majority-class
baseline, and split into sensitivity (did it find what was there) and
specificity (did it decline to invent what was not), because the whole
information content of these fields sits in the handful of negatives.

**Model uncertainty and schema failure are different outcomes.** A model
answering `unknown` has declined to guess, which the task asks for; a model
producing an object the schema rejects has failed. They are counted apart
and never averaged.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.db.models import AiOperation, VideoFeature

logger = logging.getLogger("virallab.features.vision_benchmark")

FIXTURES = (Path(__file__).resolve().parents[2]
            / "tests" / "fixtures" / "vision")
LABELS_PATH = FIXTURES / "human_labels_v2.json"
FIRST_PASS_PATH = FIXTURES / "human_labels.json"

#: Fields the labels cover. `camera_angle` and `framing` are deliberately
#: absent: they are genuinely ambiguous, and scoring against one person's
#: reading would measure agreement with that reading, not accuracy.
SCORED_BOOLEANS = (
    "performer_visible", "piano_or_keyboard_visible", "hands_visible",
    "face_visible", "sheet_music_visible", "text_overlay_present",
)
SCORED_CATEGORICAL = ("shot_distance", "setting")


@dataclass
class FieldScore:
    field_name: str
    n: int = 0
    correct: int = 0
    wrong: int = 0
    #: Model said unknown/null - a declined answer, not an error.
    unknown: int = 0
    #: Model never emitted the field at all. For an optional field under a
    #: grammar this is the expected outcome, not a model judgement.
    absent: int = 0
    #: Boolean confusion counts, against the human label as reference.
    true_pos: int = 0
    true_neg: int = 0
    false_pos: int = 0
    false_neg: int = 0
    #: Share of the labels in the commonest class - what guessing scores.
    baseline: float | None = None
    examples: list[dict] = field(default_factory=list)

    @property
    def scored(self) -> int:
        return self.correct + self.wrong

    @property
    def accuracy(self) -> float | None:
        return (round(self.correct / self.scored, 3)
                if self.scored else None)

    @property
    def sensitivity(self) -> float | None:
        """Of the openings that really contained it, how many were found."""
        d = self.true_pos + self.false_neg
        return round(self.true_pos / d, 3) if d else None

    @property
    def specificity(self) -> float | None:
        """Of the openings that did not, how many it correctly denied.

        This is the number that matters on a corpus of mostly-pianos: it is
        the only evidence that the model is looking rather than reciting the
        base rate.
        """
        d = self.true_neg + self.false_pos
        return round(self.true_neg / d, 3) if d else None

    @property
    def lift(self) -> float | None:
        """Accuracy minus what the majority class alone would have scored."""
        if self.accuracy is None or self.baseline is None:
            return None
        return round(self.accuracy - self.baseline, 3)


@dataclass
class BenchmarkReport:
    method_version: str = ""
    model: str = ""
    labels_file: str = ""
    videos_labelled: int = 0
    videos_analysed: int = 0
    videos_scored: int = 0
    schema_valid: int = 0
    schema_invalid: int = 0
    failed: int = 0
    fields: list[FieldScore] = field(default_factory=list)
    mean_latency_s: float | None = None
    mean_input_tokens: float | None = None
    peak_vram_mib: int | None = None
    total_cost_usd: float = 0.0
    overall_accuracy: float | None = None
    overall_baseline: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def overall_lift(self) -> float | None:
        if self.overall_accuracy is None or self.overall_baseline is None:
            return None
        return round(self.overall_accuracy - self.overall_baseline, 3)


def load_labels(path: Path | None = None) -> dict:
    path = path or LABELS_PATH
    if not path.exists():
        raise FileNotFoundError(f"no reference labels at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _baseline(labels: dict, name: str) -> float | None:
    vals = [v[name] for v in labels.values() if name in v]
    if not vals:
        return None
    counts: dict = {}
    for v in vals:
        key = bool(v) if isinstance(v, bool) else str(v)
        counts[key] = counts.get(key, 0) + 1
    return round(max(counts.values()) / len(vals), 3)


def run(session: Session, *, method_version: str,
        labels_path: Path | None = None) -> BenchmarkReport:
    path = labels_path or LABELS_PATH
    payload = load_labels(path)
    labels = {int(k): v for k, v in payload["labels"].items()}
    rep = BenchmarkReport(method_version=method_version,
                          labels_file=path.name,
                          videos_labelled=len(labels))

    rows = session.scalars(select(VideoFeature).where(
        VideoFeature.method_version == method_version,
        VideoFeature.status == "ok")).all()
    by_video: dict[int, dict] = {}
    for row in rows:
        if not row.feature.startswith("vision_"):
            continue
        by_video.setdefault(row.video_id, {})[
            row.feature[len("vision_"):]] = row.effective_value
    rep.videos_analysed = len(by_video)
    if rows:
        rep.model = next((r.model for r in rows if r.model), "")

    ops = session.scalars(select(AiOperation).where(
        AiOperation.video_id.in_(list(labels)))).all()
    if ops:
        rep.schema_valid = sum(1 for o in ops if o.schema_valid)
        rep.schema_invalid = sum(1 for o in ops if o.schema_valid is False)
        rep.failed = sum(1 for o in ops if o.status == "failed")
        lats = [o.latency_s for o in ops if o.latency_s]
        toks = [o.input_tokens for o in ops if o.input_tokens]
        vram = [o.peak_vram_mib for o in ops if o.peak_vram_mib]
        rep.mean_latency_s = round(sum(lats) / len(lats), 2) if lats else None
        rep.mean_input_tokens = round(sum(toks) / len(toks)) if toks else None
        rep.peak_vram_mib = max(vram) if vram else None
        rep.total_cost_usd = round(sum(o.actual_usd or 0.0 for o in ops), 6)

    scores = {name: FieldScore(field_name=name,
                               baseline=_baseline(labels, name))
              for name in SCORED_BOOLEANS + SCORED_CATEGORICAL}
    scored_videos = 0

    for video_id, truth in labels.items():
        got = by_video.get(video_id)
        if not got:
            continue
        scored_videos += 1

        for name in SCORED_BOOLEANS:
            if name not in truth:
                continue
            s = scores[name]
            s.n += 1
            if name not in got:
                s.absent += 1
                continue
            pred = got.get(name)
            if pred is None or pred == "unknown":
                s.unknown += 1
                continue
            pred, want = bool(pred), bool(truth[name])
            if pred and want:
                s.true_pos += 1
            elif not pred and not want:
                s.true_neg += 1
            elif pred and not want:
                s.false_pos += 1
            else:
                s.false_neg += 1
            if pred == want:
                s.correct += 1
            else:
                s.wrong += 1
                if len(s.examples) < 6:
                    s.examples.append(
                        {"video_id": video_id, "model": pred, "label": want,
                         "kind": "false_positive" if pred else "false_negative"})

        for name in SCORED_CATEGORICAL:
            if name not in truth:
                continue
            s = scores[name]
            s.n += 1
            if name not in got:
                s.absent += 1
                continue
            pred = got.get(name)
            if pred is None or pred == "unknown":
                s.unknown += 1
                continue
            if str(pred) == str(truth[name]):
                s.correct += 1
            else:
                s.wrong += 1
                if len(s.examples) < 6:
                    s.examples.append({"video_id": video_id, "model": pred,
                                       "label": truth[name],
                                       "kind": "mismatch"})

    rep.videos_scored = scored_videos
    rep.fields = sorted(scores.values(), key=lambda s: (
        -(s.lift if s.lift is not None else -9), s.field_name))

    total_c = sum(s.correct for s in rep.fields)
    total_s = sum(s.scored for s in rep.fields)
    rep.overall_accuracy = round(total_c / total_s, 3) if total_s else None
    # The baseline an unconditional majority-class answerer would reach on
    # exactly the items that were scored.
    base_num = sum((s.baseline or 0) * s.scored for s in rep.fields)
    rep.overall_baseline = (round(base_num / total_s, 3) if total_s else None)

    absent = [s.field_name for s in rep.fields if s.absent]
    rep.notes = [
        payload.get("_rule", ""),
        payload.get("_ordering_disclosure", ""),
        ("Accuracy is reported against each field's majority-class "
         "baseline because the presence booleans are heavily skewed on this "
         "corpus; `lift` is accuracy minus that baseline, and a lift at or "
         "below zero means the field carries no evidence that the model "
         "looked at the frames."),
        ("Specificity is the load-bearing column. Sensitivity is cheap when "
         "almost every opening contains a piano; declining to report one "
         "where there is none is not."),
        ("camera_angle and framing are not scored: they are genuinely "
         "ambiguous, and measuring them against one person's reading would "
         "report agreement with that reading as if it were accuracy."),
        ("`unknown` is a declined answer, which the task explicitly asks "
         "for in preference to a guess. It is reported separately and "
         "counted as neither correct nor wrong."),
    ]
    if absent:
        rep.notes.append(
            f"Never emitted by the model: {', '.join(absent)}. These are "
            f"optional in the task schema, so grammar-constrained decoding "
            f"permits omitting them and the model does. That is a schema "
            f"defect, not a measurement: nothing was asked, so nothing can "
            f"be scored.")
    if path == LABELS_PATH and FIRST_PASS_PATH.exists():
        rep.notes.append(
            "Pass-one labels remain at tests/fixtures/vision/"
            "human_labels.json. They were written before inference but from "
            "one frame per video; see _why_a_second_pass in the v2 file.")
    return rep
