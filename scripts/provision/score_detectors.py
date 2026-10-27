"""Score the geometric detectors against the 24 human reference labels.

Runs ON Ubuntu. M3 step one, and a decision point rather than a result:
the Gemma 3 4B benchmark scored `piano_or_keyboard_visible` at
specificity **0.000** and `sheet_music_visible` at **0.235**. If
`shared/hookdetect.py` does not clearly beat that here, there is no case
for building an 80-video held-out set around it, and M3 should go
straight to model candidates.

The reference set is `tests/fixtures/vision/human_labels_v2.json`, 24
publications, labelled from full frame strips before any inference ran.

**This is the TUNING set, not the held-out set.** Those 24 were already
used to disqualify the vision model, and the detector thresholds were
written by someone who had read that benchmark. A good score here is
therefore necessary but not sufficient — it says "worth taking to a
held-out set", never "proven". M3's promotion rule (specificity >= 0.80
and lift >= +0.10) is only meaningful on labels nobody tuned against.

Base rates matter and are reported: keyboard is true in 21 of 24 and
sheet music in 7 of 24, so "always yes" scores 0.875 on the first and
"always no" scores 0.708 on the second. Accuracy without that comparison
would be meaningless (CLAUDE.md §3.7).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/home/rastlyn/virallab")

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared import hookdetect, hookfeat  # noqa: E402

LABELS = Path("/home/rastlyn/virallab/tests/fixtures/vision/"
              "human_labels_v2.json")

#: Which detector answers which reference field.
PAIRS = (("keyboard_visible", "piano_or_keyboard_visible"),
         ("sheet_music_visible", "sheet_music_visible"))

#: What the disqualified model scored, for side-by-side reading.
MODEL_SPECIFICITY = {"piano_or_keyboard_visible": 0.000,
                     "sheet_music_visible": 0.235}


def score(pairs: list[tuple[bool, bool]]) -> dict:
    """Confusion counts and the figures that actually discriminate."""
    tp = sum(1 for p, a in pairs if p and a)
    tn = sum(1 for p, a in pairs if not p and not a)
    fp = sum(1 for p, a in pairs if p and not a)
    fn = sum(1 for p, a in pairs if not p and a)
    n = len(pairs)
    actual_true = sum(1 for _, a in pairs if a)
    baseline = max(actual_true, n - actual_true) / n if n else 0.0
    acc = (tp + tn) / n if n else 0.0
    return {
        "n": n, "tp": tp, "tn": tn, "fp": fp, "fn": fn,
        "accuracy": round(acc, 3),
        "baseline_always_commonest": round(baseline, 3),
        "lift": round(acc - baseline, 3),
        # Specificity is the number that disqualified the model: it never
        # correctly said "no".
        "specificity": (round(tn / (tn + fp), 3) if (tn + fp) else None),
        "recall": (round(tp / (tp + fn), 3) if (tp + fn) else None),
        "precision": (round(tp / (tp + fp), 3) if (tp + fp) else None),
    }


def main() -> int:
    labels = json.loads(LABELS.read_text(encoding="utf-8"))["labels"]
    eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    S = sessionmaker(eng, future=True)
    media_root = Path(os.environ["VIRALLAB_MEDIA_ROOT"])

    out: dict = {"reference": str(LABELS.name),
                 "set": "TUNING (already used to disqualify the model)",
                 "labelled": len(labels)}
    results: dict[str, list[tuple[bool, bool]]] = {
        ref: [] for _, ref in PAIRS}
    missing = []

    with S() as s:
        for vid_str, truth in labels.items():
            vid = int(vid_str)
            row = s.execute(text(
                "select media_path, media_status from videos where id=:v"),
                {"v": vid}).one_or_none()
            if row is None or row[1] != "available" or not row[0]:
                missing.append(vid)
                continue
            path = Path(row[0])
            if not path.is_absolute():
                path = media_root / path.name
            if not path.exists():
                missing.append(vid)
                continue
            frames = hookfeat.decode_frames(
                path, start=0.0, seconds=hookfeat.HOOK_SECONDS,
                fps=hookfeat.FRAME_FPS, width=hookfeat.ANALYSIS_WIDTH,
                height=hookfeat.ANALYSIS_WIDTH * 16 // 9 // 2 * 2)
            if frames.shape[0] == 0:
                missing.append(vid)
                continue
            dets = {d.name: d for d in hookdetect.detect_all(frames)}
            for det_name, ref_field in PAIRS:
                if ref_field in truth:
                    results[ref_field].append(
                        (dets[det_name].present, bool(truth[ref_field])))

    out["unscoreable"] = missing
    out["fields"] = {}
    for _, ref in PAIRS:
        sc = score(results[ref])
        sc["model_specificity_for_comparison"] = MODEL_SPECIFICITY.get(ref)
        sc["beats_model_specificity"] = (
            sc["specificity"] is not None
            and sc["specificity"] > MODEL_SPECIFICITY.get(ref, 1.0))
        sc["clears_m3_bar_on_this_set"] = (
            sc["specificity"] is not None and sc["specificity"] >= 0.80
            and sc["lift"] >= 0.10)
        out["fields"][ref] = sc

    out["verdict_note"] = (
        "Clearing the bar HERE justifies building the held-out set. It "
        "does not promote anything: these labels were used to disqualify "
        "the model and the thresholds were written with that benchmark "
        "in view, so this set can no longer measure generalisation.")
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
