"""Draw the prospective holdout, by the rule written down before it ran.

Implements `docs/benchmarks/7b-holdout-protocol.md`. Three steps, run
in order, because frames live on the server and the GPU lives on the
workstation:

    # on the SERVER - decode frames for everything eligible
    python sample_holdout.py frames --out frames.json

    # on the WORKSTATION - score them, forming strata only
    python sample_holdout.py score --frames frames.json --out scores.json

    # anywhere - draw the sample and write the manifest
    python sample_holdout.py draw --scores scores.json --out manifest.json

## The one thing that would invalidate this

Sampling the detector's *low* scores would hand the holdout only the
negatives the detector already gets right, and specificity would come
out inflated. The protocol therefore samples **across the whole score
range, including the strata where the detector is most confident** -
which is precisely where its false positives live - and carries a
weight per stratum so the enrichment can be divided back out.

Specificity and recall are conditional on the true label and are
unaffected by the enrichment. Precision, accuracy and lift are not,
and are reweighted. That asymmetry is the reason this design is
allowed at all; see §3 of the protocol.

No score is shown to the labeller. `draw` writes the manifest in
publication-id order with the scores omitted, and keeps them in a
separate file that labelling does not read.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path

#: Fixed so the draw is reproducible from the manifest alone.
SEED = 20261027

STRATA = 5
PER_STRATUM = 24

#: Fields the holdout exists to judge. Scored for stratification only.
FIELDS = ("piano_or_keyboard_visible", "hands_visible",
          "text_overlay_present")

#: Stratification is driven by the field that is worst off. Piano has
#: 4 and 6 negatives in the spent corpus; it sets the strata and the
#: other fields ride along, since a video with no keyboard in shot is
#: also disproportionately likely to lack hands.
STRATIFY_ON = "piano_or_keyboard_visible"


# ---------------------------------------------------------------------------
# step 1: frames (server)
# ---------------------------------------------------------------------------

def cmd_frames(args) -> int:
    sys.path.insert(0, "/home/rastlyn/virallab")
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from server.ai import vision_task
    from server.api.frames_api import MAX_FRAMES, encode_frames
    from server.db.models import Video

    eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    S = sessionmaker(eng, expire_on_commit=False, future=True)

    fix = Path("/home/rastlyn/virallab/tests/fixtures/vision")
    spent: set[int] = set()
    for fn in ("holdout_labels_own.json", "holdout_labels_competitor.json"):
        spent |= {int(k) for k in
                  json.loads((fix / fn).read_text())["labels"]}

    out, skipped = [], []
    with S() as s:
        rows = s.execute(text(
            "select v.id from videos v join creators c on c.id=v.creator_id "
            "where c.is_own_account is true and v.media_path is not null "
            "order by v.id")).scalars().all()
        eligible = [i for i in rows if i not in spent]
        for vid in eligible:
            v = s.get(Video, vid)
            p = Path(v.media_path) if v.media_path else None
            if p is None or not p.exists():
                skipped.append({"video_id": vid, "why": "no file"})
                continue
            stamps = vision_task.FramePlan().timestamps(
                v.duration_s)[:args.frames]
            try:
                fr = encode_frames(p, stamps, width=args.width)
            except Exception as exc:               # noqa: BLE001 - recorded
                skipped.append({"video_id": vid, "why": str(exc)[:120]})
                continue
            if fr:
                out.append({"video_id": vid, "frames": fr})
            else:
                skipped.append({"video_id": vid, "why": "no frames"})

    payload = {"_what": "frames for holdout stratification, own account",
               "_lifetime": "own media is preserved; this cache is derived",
               "width": args.width, "frames_per_video": args.frames,
               "videos": out}
    Path(args.out).write_text(json.dumps(payload), encoding="utf-8")
    print(json.dumps({"eligible": len(eligible), "encoded": len(out),
                      "skipped": len(skipped), "examples": skipped[:5],
                      "out": args.out,
                      "bytes": Path(args.out).stat().st_size}, indent=1))
    return 0


# ---------------------------------------------------------------------------
# step 2: score (workstation)
# ---------------------------------------------------------------------------

def cmd_score(args) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.provision.vision_bench import load_frames, run_owlv2

    frames = load_frames(Path(args.frames))
    scores, meta = run_owlv2(frames, args.model)
    Path(args.out).write_text(json.dumps(
        {"_what": ("OWLv2 scores used ONLY to form strata. Ranked, not "
                   "calibrated, and never shown to the labeller."),
         "model": args.model, "meta": meta,
         "scores": {str(k): v for k, v in scores.items()}},
        indent=1), encoding="utf-8")
    print(json.dumps({"scored": len(scores), "meta": meta,
                      "out": args.out}, indent=1))
    return 0


# ---------------------------------------------------------------------------
# step 3: draw
# ---------------------------------------------------------------------------

def cmd_draw(args) -> int:
    raw = json.loads(Path(args.scores).read_text(encoding="utf-8"))
    scores = {int(k): v for k, v in raw["scores"].items()}
    vals = {v: s.get(STRATIFY_ON) for v, s in scores.items()
            if s.get(STRATIFY_ON) is not None}
    if not vals:
        print("no scores for " + STRATIFY_ON)
        return 1

    lo, hi = min(vals.values()), max(vals.values())
    width = (hi - lo) / STRATA or 1.0

    def stratum_of(x: float) -> int:
        return min(STRATA - 1, int((x - lo) / width))

    buckets: dict[int, list[int]] = {i: [] for i in range(STRATA)}
    for vid, x in vals.items():
        buckets[stratum_of(x)].append(vid)

    rng = random.Random(SEED)
    chosen: list[dict] = []
    design = []
    for i in range(STRATA):
        pool = sorted(buckets[i])
        take = min(args.per_stratum, len(pool))
        picked = rng.sample(pool, take) if take else []
        # The weight that divides the enrichment back out. See §3.
        weight = (len(pool) / take) if take else None
        design.append({
            "stratum": i,
            "score_range": [round(lo + i * width, 4),
                            round(lo + (i + 1) * width, 4)],
            "eligible": len(pool), "sampled": take,
            "weight": round(weight, 4) if weight else None,
            "shortfall": max(0, args.per_stratum - len(pool)),
        })
        for vid in picked:
            chosen.append({"video_id": vid, "stratum": i, "weight": weight})

    chosen.sort(key=lambda d: d["video_id"])
    manifest = {
        "_what": ("The prospective holdout, drawn under "
                  "docs/benchmarks/7b-holdout-protocol.md."),
        "_labelling": ("Scores are deliberately ABSENT from this file. "
                       "The stratum is present because it has to be, but "
                       "the detector's opinion is not, so a label cannot "
                       "be anchored to it."),
        "_spent_by": None,
        "seed": SEED, "stratify_on": STRATIFY_ON,
        "fields": list(FIELDS),
        "design": design,
        "total": len(chosen),
        "videos": chosen,
    }
    Path(args.out).write_text(json.dumps(manifest, indent=1),
                              encoding="utf-8")

    # The manifest records the design, and the design reveals the
    # score band: stratum 4 plus its range is the detector's opinion
    # in all but name. The labeller is a person (or me) who must not
    # see it, so the labelling task is written as a SEPARATE file
    # holding nothing but publication ids in order. Reading the
    # manifest while labelling defeats the protocol; keeping the two
    # apart makes that a deliberate act rather than an accident.
    task = {
        "_what": "The labelling task. Ids only, deliberately.",
        "_why": ("No stratum and no score. A labeller who knows the "
                 "detector thinks a video scores high is no longer an "
                 "independent labeller, and the whole holdout is worth "
                 "less than the hour it took to label."),
        "video_ids": [c["video_id"] for c in chosen],
    }
    task_path = Path(args.out).with_name(
        Path(args.out).stem + "_labelling_task.json")
    task_path.write_text(json.dumps(task, indent=1), encoding="utf-8")

    print(json.dumps({"drawn": len(chosen), "design": design,
                      "out": args.out,
                      "labelling_task": str(task_path)}, indent=1))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("frames")
    f.add_argument("--out", required=True)
    f.add_argument("--width", type=int, default=512)
    f.add_argument("--frames", type=int, default=4)
    f.set_defaults(fn=cmd_frames)

    s = sub.add_parser("score")
    s.add_argument("--frames", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--model", default="google/owlv2-base-patch16-ensemble")
    s.set_defaults(fn=cmd_score)

    d = sub.add_parser("draw")
    d.add_argument("--scores", required=True)
    d.add_argument("--out", required=True)
    d.add_argument("--per-stratum", type=int, default=PER_STRATUM)
    d.set_defaults(fn=cmd_draw)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
