"""Four ways to answer the same visual question, measured side by side.

Runs on the WORKSTATION, against frames cached by
`cache_holdout_frames.py` so every approach sees identical images.

## Why this exists

The vision stack has two fields that work and four that do not, and
the diagnosis is specific: for `piano_or_keyboard_visible` the
language model answers "yes" on 79 of 80 publications whatever the
pixels show, its confidence is identical when right and wrong
(0.932 against 0.933), and it self-flagged none of its 78 errors.
That is not a threshold that needs moving. It is a model that never
evaluates the question, and no prompt will fix it - the
negative-example arm moved it from 79 to 78.

The structural mistake was asking a captioning model, under a grammar
that forces a bare boolean, to make a classification decision. A
boolean carries no gradation, so there is nothing to calibrate.

## What is compared, and how

* **gemma** - the committed baseline, read from the prediction
  fixtures rather than re-run, so it is exactly the arm the published
  benchmark scored.
* **clip** - OpenCLIP zero-shot. Several positive and several
  negative phrasings per field; the score is the margin between the
  best positive and the best negative similarity.
* **owlv2** - open-vocabulary detection. The score is the highest box
  confidence for the object's text query anywhere in the frame.
* **deterministic** - `shared/hookdetect`, kept in the comparison
  because a measured failure is a result and dropping it would
  quietly improve the field.

**A similarity is not a probability.** CLIP margins and OWLv2 box
scores are ranked, not calibrated, so neither is reported as a
confidence. Each is turned into a decision by a threshold, and the
threshold is chosen on one cohort and applied to the other - both
directions - so no number here is read off the data it was tuned on.
The in-cohort figure is printed beside it, clearly marked, because
the gap between the two is the honest measure of how much the
threshold is worth.

## Results

Written up in `docs/benchmarks/7-detector-comparison.md`, with the
metrics committed to `tests/fixtures/vision/detector_comparison.json`
and asserted by `tests/server/test_detector_comparison.py`.

The headline is not the ranking. Three of the five fields have too
few negatives in this corpus - 4 and 6 for piano, 7 and 3 for hands,
1 for text overlay in the own cohort - for specificity to be
measurable at all. `--owl-discriminative` is a failed experiment kept
for reproducibility and is off by default.

    python scripts/provision/vision_bench.py --approaches clip,gemma
    python scripts/provision/vision_bench.py --json --out bench.json
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "vision"

#: The presence questions worth comparing. `text_overlay_present` is
#: here as a control: it already works, so an approach that breaks it
#: is telling us something.
FIELDS = ("piano_or_keyboard_visible", "hands_visible",
          "sheet_music_visible", "face_visible", "text_overlay_present")

#: Several phrasings each way. One prompt is a sample of size one:
#: CLIP is famously sensitive to wording, and averaging over a few
#: honest phrasings measures the idea rather than the sentence.
CLIP_PROMPTS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "piano_or_keyboard_visible": (
        ("a photo of piano keys",
         "a close-up of a piano keyboard with black and white keys",
         "hands playing on a piano keyboard",
         "a musical keyboard instrument with visible keys"),
        ("a photo with no piano and no keyboard",
         "a person talking to camera with no instrument",
         "an outdoor scene with no musical instrument",
         "a concert stage photographed from a distance",
         "sheet music on a stand with no keyboard visible"),
    ),
    "hands_visible": (
        ("a photo showing human hands",
         "close-up of fingers",
         "hands resting on a surface"),
        ("a photo with no hands visible",
         "a landscape with no people",
         "a face portrait with no hands in frame"),
    ),
    "sheet_music_visible": (
        ("printed sheet music with musical notation",
         "a page of music with staves and notes",
         "a musical score on a stand"),
        ("a photo with no sheet music",
         "a blank white page with no notation",
         "an open book that is not sheet music",
         "a screen showing an animation, not notation"),
    ),
    "face_visible": (
        ("a photo of a person's face",
         "a human face looking at the camera",
         "a portrait of a person"),
        ("a photo with no face visible",
         "the back of someone's head",
         "a close-up of hands with no face"),
    ),
    "text_overlay_present": (
        ("a video frame with large caption text overlaid on it",
         "a screenshot with subtitle text burned in",
         "an image with big words written across it"),
        ("a video frame with no text on it",
         "a clean photo with no captions or words"),
    ),
}

#: Text queries for the detector. Only the objects a detector can
#: sensibly be asked for; "text overlay" is not an object.
OWL_QUERIES: dict[str, tuple[str, ...]] = {
    "piano_or_keyboard_visible": ("a piano keyboard", "piano keys"),
    "hands_visible": ("a human hand", "hands"),
    "sheet_music_visible": ("sheet music", "a page of musical notation"),
    "face_visible": ("a human face",),
}

#: Queries whose score is SUBTRACTED, to separate "the keys are
#: visible" from "an instrument is present".
#:
#: Diagnosed from the failures, not guessed. Asked for "a piano
#: keyboard", OWLv2 scores a grand piano shot side-on from the
#: stalls (259) at 0.459 and a piano filmed over its open soundboard
#: (761) at 0.429 - both have no keys in frame and both have an
#: obvious piano in frame. The detector is finding the instrument and
#: answering a question nobody asked. Subtracting an explicit
#: "instrument but no keys" query tests whether that confusion is
#: separable.
OWL_NEGATIVE_QUERIES: dict[str, tuple[str, ...]] = {
    "piano_or_keyboard_visible": (
        "a grand piano seen from the side with no keys visible",
        "the open lid and strings of a piano",
        "a concert stage photographed from far away"),
}


def load_labels() -> tuple[dict[int, dict], dict[int, str]]:
    truth, cohort = {}, {}
    for name, which in (("own", "own"), ("competitor", "competitor")):
        doc = json.loads(
            (FIX / f"holdout_labels_{name}.json").read_text(encoding="utf-8"))
        for k, v in doc["labels"].items():
            truth[int(k)] = v
            cohort[int(k)] = which
    return truth, cohort


def load_frames(path: Path) -> dict[int, list]:
    from PIL import Image

    doc = json.loads(path.read_text(encoding="utf-8"))
    out: dict[int, list] = {}
    for row in doc["videos"]:
        imgs = []
        for f in row["frames"]:
            raw = base64.b64decode(f["jpeg_b64"])
            imgs.append(Image.open(io.BytesIO(raw)).convert("RGB"))
        out[int(row["video_id"])] = imgs
    return out


def vram_mib() -> int | None:
    import subprocess
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10)
        return int(out.stdout.strip().splitlines()[0])
    except Exception:                         # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# approaches. each returns {video_id: {field: score}} - a SCORE, not a
# decision, because the decision is the part we want to control.
# ---------------------------------------------------------------------------

def run_gemma() -> tuple[dict, dict]:
    """The committed baseline. Booleans, so the 'score' is 0 or 1."""
    scores: dict[int, dict] = {}
    for name in ("own", "competitor"):
        doc = json.loads(
            (FIX / f"holdout_predictions_{name}.json").read_text(
                encoding="utf-8"))
        for r in doc["results"]:
            if r["arm"] != "original" or r["status"] != "ok":
                continue
            scores[int(r["video_id"])] = {
                f: (1.0 if bool(r["data"].get(f)) else 0.0)
                for f in FIELDS if f in r["data"]}
    return scores, {"seconds": None, "vram_mib": None,
                    "note": "read from the committed fixtures; "
                            "not re-run"}


def run_clip(frames: dict[int, list], model_name: str,
             pretrained: str) -> tuple[dict, dict]:
    import open_clip
    import torch

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    base = vram_mib()
    t0 = time.monotonic()
    model, _, preprocess = open_clip.create_model_and_transforms(
        model_name, pretrained=pretrained, device=dev)
    model.eval()
    tok = open_clip.get_tokenizer(model_name)
    load_s = time.monotonic() - t0

    text_feats: dict[str, tuple] = {}
    with torch.no_grad():
        for field, (pos, neg) in CLIP_PROMPTS.items():
            p = model.encode_text(tok(list(pos)).to(dev))
            n = model.encode_text(tok(list(neg)).to(dev))
            p = p / p.norm(dim=-1, keepdim=True)
            n = n / n.norm(dim=-1, keepdim=True)
            text_feats[field] = (p, n)

    peak = base or 0
    t0 = time.monotonic()
    scores: dict[int, dict] = {}
    with torch.no_grad():
        for vid, imgs in frames.items():
            batch = torch.stack([preprocess(i) for i in imgs]).to(dev)
            feats = model.encode_image(batch)
            feats = feats / feats.norm(dim=-1, keepdim=True)
            row = {}
            for field, (p, n) in text_feats.items():
                # Best positive minus best negative, per frame, then
                # the best frame. "Visible in ANY frame" is the label
                # rule, so the aggregation has to be a max.
                sp = (feats @ p.T).max(dim=1).values
                sn = (feats @ n.T).max(dim=1).values
                row[field] = float((sp - sn).max().item())
            scores[vid] = row
            peak = max(peak, vram_mib() or 0)
    return scores, {
        "seconds": round(time.monotonic() - t0, 1),
        "load_seconds": round(load_s, 1),
        "vram_mib": (peak - (base or 0)) if base is not None else None,
        "model": f"{model_name}/{pretrained}",
        "score_semantics": ("best positive minus best negative cosine "
                            "similarity, maximum over frames. A margin, "
                            "NOT a probability."),
    }


def run_owlv2(frames: dict[int, list],
              name: str = "google/owlv2-base-patch16-ensemble",
              discriminative: bool = False) -> tuple[dict, dict]:
    import torch
    from transformers import Owlv2ForObjectDetection, Owlv2Processor

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    base = vram_mib()
    t0 = time.monotonic()
    proc = Owlv2Processor.from_pretrained(name)
    model = Owlv2ForObjectDetection.from_pretrained(name).to(dev).eval()
    load_s = time.monotonic() - t0

    fields = list(OWL_QUERIES)
    queries = [list(OWL_QUERIES[f]) for f in fields]
    negs = [list(OWL_NEGATIVE_QUERIES.get(f, ())) if discriminative
            else [] for f in fields]
    flat, owner, sign = [], [], []
    for fi, (qs, ns) in enumerate(zip(queries, negs)):
        for q in qs:
            flat.append(q); owner.append(fi); sign.append(1)
        for q in ns:
            flat.append(q); owner.append(fi); sign.append(-1)

    peak = base or 0
    t0 = time.monotonic()
    scores: dict[int, dict] = {}
    with torch.no_grad():
        for vid, imgs in frames.items():
            best = [0.0] * len(fields)
            worst = [0.0] * len(fields)
            for img in imgs:
                inputs = proc(text=[flat], images=img,
                              return_tensors="pt").to(dev)
                out = model(**inputs)
                logits = out.logits[0]           # (boxes, queries)
                probs = torch.sigmoid(logits)
                per_query = probs.max(dim=0).values
                for qi, fi in enumerate(owner):
                    v = float(per_query[qi].item())
                    if sign[qi] > 0:
                        best[fi] = max(best[fi], v)
                    else:
                        worst[fi] = max(worst[fi], v)
            scores[vid] = {f: best[i] - worst[i]
                           for i, f in enumerate(fields)}
            peak = max(peak, vram_mib() or 0)
    return scores, {
        "seconds": round(time.monotonic() - t0, 1),
        "load_seconds": round(load_s, 1),
        "vram_mib": (peak - (base or 0)) if base is not None else None,
        "model": name,
        "score_semantics": ("highest box confidence for the object's "
                            "text query, maximum over frames. Ranked, "
                            "NOT calibrated."),
    }


def run_deterministic(frames: dict[int, list]) -> tuple[dict, dict]:
    import numpy as np

    from shared import hookdetect

    t0 = time.monotonic()
    scores: dict[int, dict] = {}
    for vid, imgs in frames.items():
        arr = np.stack([np.asarray(i) for i in imgs])
        dets = {d.name: d for d in hookdetect.detect_all(arr)}
        scores[vid] = {
            "piano_or_keyboard_visible":
                float(dets["keyboard_visible"].score),
            "sheet_music_visible":
                float(dets["sheet_music_visible"].score),
        }
    return scores, {
        "seconds": round(time.monotonic() - t0, 1),
        "vram_mib": 0,
        "model": f"hookdetect {hookdetect.METHOD_VERSION}",
        "score_semantics": "detector score, already known to fail",
    }


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------

def confusion(pairs: list[tuple[bool, bool]]) -> dict:
    tp = sum(1 for p, a in pairs if p and a)
    tn = sum(1 for p, a in pairs if not p and not a)
    fp = sum(1 for p, a in pairs if p and not a)
    fn = sum(1 for p, a in pairs if not p and a)
    n = len(pairs)
    pos = tp + fn
    base = max(pos, n - pos) / n if n else 0.0
    acc = (tp + tn) / n if n else 0.0
    return {
        "n": n, "tp": tp, "tn": tn, "fp": fp, "fn": fn,
        "accuracy": round(acc, 3),
        "baseline": round(base, 3),
        "lift": round(acc - base, 3),
        "precision": round(tp / (tp + fp), 3) if (tp + fp) else None,
        "recall": round(tp / (tp + fn), 3) if (tp + fn) else None,
        "specificity": round(tn / (tn + fp), 3) if (tn + fp) else None,
        "informative": 0 < pos < n,
    }


def best_threshold(scores: dict[int, dict], truth: dict[int, dict],
                   field: str, ids: list[int]) -> float | None:
    """The threshold maximising lift on the given ids.

    Lift rather than accuracy, because every field here is skewed and
    accuracy would pick the threshold that says "always yes".
    """
    vals = sorted({scores[v][field] for v in ids
                   if v in scores and field in scores[v]})
    if len(vals) < 2:
        return None
    cands = [(a + b) / 2 for a, b in zip(vals, vals[1:])]
    best, best_lift = None, -2.0
    for t in cands:
        pairs = [(scores[v][field] >= t, bool(truth[v][field]))
                 for v in ids
                 if v in scores and field in scores[v]
                 and field in truth[v]]
        c = confusion(pairs)
        if c["lift"] > best_lift:
            best, best_lift = t, c["lift"]
    return best


def evaluate(scores: dict[int, dict], truth: dict[int, dict],
             cohort: dict[int, str]) -> dict:
    own = [v for v in scores if cohort.get(v) == "own"]
    comp = [v for v in scores if cohort.get(v) == "competitor"]
    out: dict = {}
    for field in FIELDS:
        have = [v for v in scores
                if field in scores.get(v, {}) and field in truth.get(v, {})]
        if not have:
            continue
        entry: dict = {}
        for tune_name, tune_ids, test_name, test_ids in (
                ("own", own, "competitor", comp),
                ("competitor", comp, "own", own)):
            t = best_threshold(scores, truth, field,
                               [v for v in tune_ids if v in have])
            if t is None:
                continue
            test = [v for v in test_ids if v in have]
            pairs = [(scores[v][field] >= t, bool(truth[v][field]))
                     for v in test]
            entry[f"tuned_on_{tune_name}_tested_on_{test_name}"] = {
                "threshold": round(t, 4), **confusion(pairs)}
        t_all = best_threshold(scores, truth, field, have)
        if t_all is not None:
            pairs = [(scores[v][field] >= t_all, bool(truth[v][field]))
                     for v in have]
            entry["in_sample_optimistic"] = {
                "threshold": round(t_all, 4), **confusion(pairs),
                "warning": ("threshold chosen on these same labels; "
                            "this is the number to distrust")}
        out[field] = entry
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--frames", default="")
    ap.add_argument("--approaches", default="gemma,clip,owlv2,deterministic")
    ap.add_argument("--clip-model", default="ViT-B-32")
    ap.add_argument("--owl-discriminative", action="store_true",
                    help="subtract an 'instrument but no keys' query")
    ap.add_argument("--owl-model",
                    default="google/owlv2-base-patch16-ensemble")
    ap.add_argument("--clip-pretrained", default="laion2b_s34b_b79k")
    ap.add_argument("--out", default="")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dump-scores", action="store_true",
                    help="include per-video scores so failures can be "
                         "inspected individually")
    args = ap.parse_args(argv)

    truth, cohort = load_labels()
    frames_path = Path(args.frames) if args.frames else None
    frames: dict[int, list] = {}
    wanted = [a.strip() for a in args.approaches.split(",") if a.strip()]
    if any(a != "gemma" for a in wanted):
        if frames_path is None or not frames_path.exists():
            print("--frames is required for every approach except gemma")
            return 2
        frames = load_frames(frames_path)

    report: dict = {
        "labels": len(truth),
        "cohorts": {"own": sum(1 for c in cohort.values() if c == "own"),
                    "competitor": sum(1 for c in cohort.values()
                                      if c == "competitor")},
        "approaches": {},
        "how_to_read": {
            "scores_are_not_probabilities": (
                "CLIP margins and OWLv2 box scores are ranked, not "
                "calibrated. Neither is reported as a confidence."),
            "which_number_to_trust": (
                "`tuned_on_X_tested_on_Y` chooses the threshold on one "
                "cohort and measures it on the other. "
                "`in_sample_optimistic` chooses and measures on the "
                "same labels and is the number to distrust."),
            "the_bar": ("M3's pre-registered rule: specificity >= 0.80 "
                        "and lift >= +0.10, on a holdout nothing was "
                        "tuned on. Nothing here is that holdout."),
        },
    }

    for name in wanted:
        try:
            if name == "gemma":
                scores, meta = run_gemma()
            elif name == "clip":
                scores, meta = run_clip(frames, args.clip_model,
                                        args.clip_pretrained)
            elif name == "owlv2":
                scores, meta = run_owlv2(
                    frames, args.owl_model, args.owl_discriminative)
            elif name == "deterministic":
                scores, meta = run_deterministic(frames)
            else:
                continue
        except Exception as exc:              # noqa: BLE001 - recorded
            report["approaches"][name] = {
                "error": f"{type(exc).__name__}: {exc}"[:400]}
            continue
        report["approaches"][name] = {
            "meta": meta,
            "videos_scored": len(scores),
            "fields": evaluate(scores, truth, cohort),
        }
        if args.dump_scores:
            # Per-video scores, so a failure can be looked at rather
            # than inferred from a confusion matrix.
            report["approaches"][name]["scores"] = {
                str(v): r for v, r in scores.items()}

    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1),
                                  encoding="utf-8")
    if args.json:
        print(json.dumps(report, indent=1))
    else:
        _print_table(report)
    return 0


def _print_table(report: dict) -> None:
    print(f"{report['labels']} labelled publications "
          f"({report['cohorts']['own']} own, "
          f"{report['cohorts']['competitor']} competitor)\n")
    for name, a in report["approaches"].items():
        if "error" in a:
            print(f"=== {name}: FAILED - {a['error']}\n")
            continue
        m = a["meta"]
        print(f"=== {name}  {m.get('model', '')}  "
              f"{m.get('seconds')}s  {m.get('vram_mib')} MiB")
        hdr = (f"  {'field':<28}{'thr':>7}{'prec':>6}{'rec':>6}"
               f"{'spec':>6}{'lift':>7}  (tuned->tested)")
        print(hdr)
        for field, e in a["fields"].items():
            for key, r in e.items():
                if key == "in_sample_optimistic":
                    continue
                arrow = key.replace("tuned_on_", "").replace(
                    "_tested_on_", "->")
                print(f"  {field:<28}{r.get('threshold', 0):>7.3f}"
                      f"{_f(r['precision'])}{_f(r['recall'])}"
                      f"{_f(r['specificity'])}{r['lift']:>7.3f}  {arrow}")
        print()


def _f(x) -> str:
    return f"{x:>6.3f}" if isinstance(x, float) else f"{'-':>6}"


if __name__ == "__main__":
    raise SystemExit(main())
