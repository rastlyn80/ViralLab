"""Score the prospective holdout, by the rule written down before it ran.

Implements `docs/benchmarks/7b-holdout-protocol.md`, including the
2026-11-13 addendum that fixed the open choices before any number
existed. Nothing here is tunable: thresholds come from the spent M3
corpus, weights come from the seeded draw, and the bars are the ones
pre-registered on 2026-10-27.

    # on the SERVER - export the owner's answers
    python score_holdout.py labels --out holdout_answers.json

    # anywhere - score, with no freedom left
    python score_holdout.py score --arm holdout_arm_scores.json
        --manifest manifest.json --labels holdout_answers.json
        --out tests/fixtures/vision/holdout_result.json

## The frame set, which is the whole reason this script exists

`holdout_scores.json` was computed on 4 frames and is good for one
thing: forming strata. The owner labelled from up to 6 frames, and
the M3 corpus that fixed every threshold was scored on 6 as well. An
OWLv2 score is a maximum over frames, so the 4-frame numbers are a
different quantity and scoring against them would quietly
miscalibrate the test. See section 7.1 of the protocol. The `--arm`
file must be scored on the labelled frame set.

## What is unweighted and what is not

Specificity and recall are conditional on the true label, so the
stratified draw does not bias them and they are reported unweighted.
Precision, accuracy, base rate and therefore lift all depend on
prevalence, and the draw deliberately over-sampled the detector's
confident strata, so those are weighted back to the eligible
population of 443. Mixing the two up is the error this design exists
to avoid; see section 3.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from pathlib import Path

#: Pre-registered on 2026-10-27, not negotiable here.
BAR_SPECIFICITY = 0.80
BAR_LIFT = 0.10

#: Fixed in section 7.2 before the first number was computed. The
#: primary decides promotion; the secondary is reported beside it
#: and decides nothing.
THRESHOLDS = {
    "piano_or_keyboard_visible": {
        "primary": ("tuned_on_competitor_tested_on_own", 0.3778),
        "secondary": ("tuned_on_own_tested_on_competitor", 0.3117),
    },
    "hands_visible": {
        "primary": ("tuned_on_competitor_tested_on_own", 0.1285),
        "secondary": ("tuned_on_own_tested_on_competitor", 0.0755),
    },
}

#: Asked of the owner, but OWLv2 has no text-overlay query, so there
#: is no arm to score. Reported as unmeasured rather than failed.
NO_ARM = ("text_overlay_present",)

BOOTSTRAP_REPS = 2000
BOOTSTRAP_SEED = 20261113


# ---------------------------------------------------------------------------
# step 1: the answer key (server)
# ---------------------------------------------------------------------------

def cmd_labels(args) -> int:
    sys.path.insert(0, "/home/rastlyn/virallab")
    from sqlalchemy import create_engine, text

    eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    out: dict[str, dict[str, str]] = {}
    with eng.begin() as c:
        rows = c.execute(text(
            "select video_id, field, owner_label from label_reviews "
            "where cohort = 'holdout' order by video_id, field"))
        for r in rows:
            out.setdefault(str(r.video_id), {})[r.field] = r.owner_label
    payload = {
        "_what": ("The owner's answers to the prospective holdout. "
                  "Written blind: no model label, no score and no "
                  "stratum reached the interface."),
        "_source": "label_reviews where cohort = 'holdout'",
        "_counts": {"publications": len(out),
                    "answers": sum(len(v) for v in out.values())},
        "answers": out,
    }
    Path(args.out).write_text(json.dumps(payload, indent=1),
                              encoding="utf-8")
    print(json.dumps(payload["_counts"], indent=1))
    return 0


# ---------------------------------------------------------------------------
# statistics
# ---------------------------------------------------------------------------

def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    """Wilson score interval. Degrades honestly rather than crashing."""
    if n <= 0:
        return None
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, centre - half), 4),
            round(min(1.0, centre + half), 4)]


def weighted_cells(rows: list[dict]) -> dict:
    """Prevalence-dependent quantities, reweighted to the population."""
    tot = sum(r["w"] for r in rows)
    if tot <= 0:
        return {}
    pos = sum(r["w"] for r in rows if r["y"])
    correct = sum(r["w"] for r in rows if r["y"] == r["pred"])
    said_yes = sum(r["w"] for r in rows if r["pred"])
    hit = sum(r["w"] for r in rows if r["pred"] and r["y"])
    base = pos / tot
    majority = max(base, 1 - base)
    accuracy = correct / tot
    return {
        "base_rate": base,
        "majority_class_accuracy": majority,
        "accuracy": accuracy,
        "precision": (hit / said_yes) if said_yes else None,
        "lift": accuracy - majority,
        # What a FLAWLESS detector would score on this population.
        # A lift bar above this is unreachable by anything, and the
        # question is then the problem rather than the detector.
        "max_attainable_lift": 1.0 - majority,
    }


def bootstrap(rows: list[dict], reps: int = BOOTSTRAP_REPS) -> dict:
    """Stratified bootstrap: resample within strata, never across them."""
    by_stratum: dict[int, list[dict]] = {}
    for r in rows:
        by_stratum.setdefault(r["stratum"], []).append(r)
    rng = random.Random(BOOTSTRAP_SEED)
    accs: list[float] = []
    lifts: list[float] = []
    for _ in range(reps):
        draw: list[dict] = []
        for pool in by_stratum.values():
            draw += [pool[rng.randrange(len(pool))] for _ in pool]
        cells = weighted_cells(draw)
        if cells:
            accs.append(cells["accuracy"])
            lifts.append(cells["lift"])
    if not accs:
        return {}

    def pct(xs: list[float], q: float) -> float:
        xs = sorted(xs)
        return xs[min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))]

    return {
        "accuracy_ci95": [round(pct(accs, 0.025), 4),
                          round(pct(accs, 0.975), 4)],
        "lift_ci95": [round(pct(lifts, 0.025), 4),
                      round(pct(lifts, 0.975), 4)],
        "replicates": len(accs),
    }


def score_field(rows: list[dict]) -> dict:
    """One field at one threshold, already thresholded into `pred`."""
    tp = sum(1 for r in rows if r["y"] and r["pred"])
    fn = sum(1 for r in rows if r["y"] and not r["pred"])
    tn = sum(1 for r in rows if not r["y"] and not r["pred"])
    fp = sum(1 for r in rows if not r["y"] and r["pred"])
    npos, nneg = tp + fn, tn + fp
    out: dict = {
        "n": len(rows), "positives": npos, "negatives": nneg,
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        # Conditional on the true label: the draw does not touch these.
        "specificity": (tn / nneg) if nneg else None,
        "specificity_ci95": wilson(tn, nneg),
        "recall": (tp / npos) if npos else None,
        "recall_ci95": wilson(tp, npos),
    }
    out.update(weighted_cells(rows))
    out.update(bootstrap(rows))
    for k in ("specificity", "recall", "base_rate", "accuracy",
              "precision", "lift", "majority_class_accuracy",
              "max_attainable_lift"):
        if isinstance(out.get(k), float):
            out[k] = round(out[k], 4)
    out["clears_specificity"] = (out["specificity"] is not None
                                 and out["specificity"] >= BAR_SPECIFICITY)
    out["clears_lift"] = (out.get("lift") is not None
                          and out["lift"] >= BAR_LIFT)
    out["clears_both"] = bool(out["clears_specificity"]
                              and out["clears_lift"])
    out["lift_bar_attainable"] = (
        out.get("max_attainable_lift") is not None
        and out["max_attainable_lift"] >= BAR_LIFT)
    return out


# ---------------------------------------------------------------------------
# step 2: score
# ---------------------------------------------------------------------------

def cmd_score(args) -> int:
    arm = json.loads(Path(args.arm).read_text(encoding="utf-8"))
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    answers = json.loads(
        Path(args.labels).read_text(encoding="utf-8"))["answers"]

    scores = {int(k): v for k, v in arm["scores"].items()}
    weight = {v["video_id"]: v["weight"] for v in manifest["videos"]}
    stratum = {v["video_id"]: v["stratum"] for v in manifest["videos"]}

    drawn = set(weight)
    answered = {int(k) for k in answers}
    if drawn != answered:
        raise SystemExit(
            f"the drawn set and the answered set differ: "
            f"{len(drawn - answered)} unanswered, "
            f"{len(answered - drawn)} answered but not drawn")
    if not drawn <= set(scores):
        raise SystemExit(
            f"{len(drawn - set(scores))} drawn publications have no arm "
            f"score; the arm must cover the whole draw")

    fields: dict[str, dict] = {}
    for field, thr in THRESHOLDS.items():
        said = {int(v): a[field] for v, a in answers.items()
                if a.get(field)}
        unsure = sorted(v for v, a in said.items() if a == "cannot_tell")
        cell: dict = {
            "answers": {"yes": sum(1 for a in said.values() if a == "yes"),
                        "no": sum(1 for a in said.values() if a == "no"),
                        "cannot_tell": len(unsure)},
            "cannot_tell_ids": unsure,
            "arms": {},
        }
        for kind in ("primary", "secondary"):
            name, cut = thr[kind]
            rows = [{"video_id": v, "y": said[v] == "yes",
                     "pred": scores[v][field] >= cut,
                     "w": weight[v], "stratum": stratum[v]}
                    for v in sorted(said) if said[v] in ("yes", "no")]
            # Section 7.3: the worst-case assignment, not the kind one.
            adversarial = rows + [
                {"video_id": v, "y": not (scores[v][field] >= cut),
                 "pred": scores[v][field] >= cut,
                 "w": weight[v], "stratum": stratum[v]}
                for v in unsure]
            cell["arms"][kind] = {
                "threshold": cut, "threshold_source": name,
                "primary_analysis": score_field(rows),
                "cannot_tell_adversarial": score_field(adversarial),
            }
        decisive = cell["arms"]["primary"]
        cell["promoted"] = bool(
            decisive["primary_analysis"]["clears_both"]
            and decisive["cannot_tell_adversarial"]["clears_both"])
        cell["promotion_scope"] = (
            "own account only - the holdout has no competitor half "
            "(protocol 7.4); Observed under ADR 0011, not Validated"
            if cell["promoted"] else "not promoted")
        fields[field] = cell

    for field in NO_ARM:
        said = {int(v): a[field] for v, a in answers.items()
                if a.get(field)}
        fields[field] = {
            "answers": {
                "yes": sum(1 for a in said.values() if a == "yes"),
                "no": sum(1 for a in said.values() if a == "no"),
                "cannot_tell": sum(1 for a in said.values()
                                   if a == "cannot_tell")},
            "arms": {},
            "promoted": False,
            "promotion_scope": "not promoted",
            "unmeasured": ("OWLv2 scores no text-overlay query, so there "
                           "is no arm to score. Unmeasured, not failed."),
        }

    frames_note = None
    if args.strata_scores:
        import statistics
        four = json.loads(Path(args.strata_scores).read_text(
            encoding="utf-8"))["scores"]
        frames_note = {}
        for field, thr in THRESHOLDS.items():
            cut = thr["primary"][1]
            deltas = [scores[int(k)][field] - four[k][field]
                      for k in four if int(k) in scores]
            flips = sum(
                1 for k in four if int(k) in scores
                and ((scores[int(k)][field] >= cut)
                     != (four[k][field] >= cut)))
            frames_note[field] = {
                "mean_delta_6_minus_4_frames": round(
                    statistics.mean(deltas), 4),
                "publications_changing_side_of_threshold": flips,
                "of": len(deltas),
            }

    result = {
        "_what": ("The prospective holdout, scored under "
                  "docs/benchmarks/7b-holdout-protocol.md and its "
                  "2026-11-13 addendum."),
        "_spent_by": ("docs/benchmarks/7e-holdout-result.md - this "
                      "holdout has now been used to decide promotion "
                      "and may not decide anything else."),
        "_cohort": ("own account only; 443 eligible publications, none "
                    "of them a competitor's (protocol 7.4)"),
        "_frames": ("arm scored on FramePlan[:MAX_FRAMES] width 512, "
                    "the frames the owner judged (protocol 7.1)"),
        "bars": {"specificity": BAR_SPECIFICITY, "lift": BAR_LIFT},
        "model": arm.get("model"),
        "meta": arm.get("meta"),
        "design": manifest["design"],
        "n_publications": len(drawn),
        # The draw itself, so the published cells can be recomputed
        # from committed files alone - no server, no GPU, no manifest
        # left on a machine that gets wiped.
        "sample": [{"video_id": v, "stratum": stratum[v],
                    "weight": weight[v]} for v in sorted(drawn)],
        "frame_set_effect": frames_note,
        "fields": fields,
    }
    Path(args.out).write_text(json.dumps(result, indent=1),
                              encoding="utf-8")

    for field, cell in result["fields"].items():
        print(f"\n{field}")
        a = cell["answers"]
        print(f"  answers: {a['yes']} yes, {a['no']} no, "
              f"{a['cannot_tell']} cannot tell")
        if cell.get("unmeasured"):
            print("  " + cell["unmeasured"])
            continue
        for kind, arm_cell in cell["arms"].items():
            p = arm_cell["primary_analysis"]
            adv = arm_cell["cannot_tell_adversarial"]
            print(f"  {kind} threshold {arm_cell['threshold']} "
                  f"({arm_cell['threshold_source']})")
            print(f"    specificity {p['specificity']} "
                  f"{p['specificity_ci95']}  "
                  f"recall {p['recall']} {p['recall_ci95']}")
            print(f"    weighted base rate {p['base_rate']}, "
                  f"accuracy {p['accuracy']} {p.get('accuracy_ci95')}, "
                  f"lift {p['lift']:+} {p.get('lift_ci95')}")
            print(f"    clears both bars: {p['clears_both']} "
                  f"(adversarial: {adv['clears_both']})")
            if not p["lift_bar_attainable"]:
                print(f"    the lift bar is UNREACHABLE here: a "
                      f"flawless detector scores at most "
                      f"{p['max_attainable_lift']:+}")
        print("  -> " + cell["promotion_scope"])
    print(f"\nwritten to {args.out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    lb = sub.add_parser("labels")
    lb.add_argument("--out", required=True)
    lb.set_defaults(fn=cmd_labels)

    sc = sub.add_parser("score")
    sc.add_argument("--arm", required=True)
    sc.add_argument("--manifest", required=True)
    sc.add_argument("--labels", required=True)
    sc.add_argument("--strata-scores", default="",
                    help="the 4-frame stratification scores, to record "
                         "how much the frame-set correction of section "
                         "7.1 actually moved")
    sc.add_argument("--out", required=True)
    sc.set_defaults(fn=cmd_score)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
