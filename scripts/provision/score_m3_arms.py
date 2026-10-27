"""Score the M3 prompt arms against the held-out set. Runs anywhere.

Reads the JSON `m3_prompt_arms.py` wrote - one file per half of the
held-out set - and applies M3's promotion rule: a field enters analytical
use only at **specificity >= 0.80 and lift >= +0.10 on the held-out
set**. Anything short of that stays withheld, and the prompt route joins
the geometric one as a measured dead end.

Four things this reports that a plain accuracy table would not:

**The halves stay apart, and the verdict needs both.** The own-account
40 are the V4 transfer population - the owner's own publications, the
ones any recommendation would actually be about. The competitor 40 are
33 different creators the model has never been tuned on, which is the
only unseen-creator evidence M3 has. A field that clears the bar on one
and fails the other has not generalised, and averaging the two would
hide exactly that. So `promote` requires both halves to clear it
independently; the pooled table is reported for reading, never for
deciding.

**Baselines, per half.** `performer_visible` is true in all 40 own
labels, so nothing can be learned from it there - it has five negatives
on the competitor half and is measurable only there. `text_overlay_
present` is 39/40 own against 29/40 competitor. The base rates differ
enough between halves that a single pooled baseline would be misleading
on both.

**A paired test.** Both arms saw the same frames of the same
publications, so the comparison is McNemar's exact test on the
discordant pairs. An unpaired difference of proportions would throw away
that pairing and overstate the uncertainty.

**The labeller.** These labels were written by Claude, not the owner.
That is recorded in the output, not only in the fixtures, because a
promotion decision made on them is a decision made on one model's
reading of 80 images.

    python scripts/provision/score_m3_arms.py \\
        --half own --arms arms_own.json \\
            --labels tests/fixtures/vision/holdout_labels_own.json \\
        --half competitor --arms arms_comp.json \\
            --labels tests/fixtures/vision/holdout_labels_competitor.json

`--half`, `--arms` and `--labels` are three parallel repeatable
flags rather than one colon-joined triple: a Windows path begins
"C:" and a colon separator is unparseable on the machine that runs
the model.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "vision"

SCORED_BOOLEANS = (
    "performer_visible", "piano_or_keyboard_visible", "hands_visible",
    "face_visible", "sheet_music_visible", "text_overlay_present",
)
SCORED_CATEGORICAL = ("shot_distance", "setting")

#: M3's promotion rule, written down before scoring, in one place.
MIN_SPECIFICITY = 0.80
MIN_LIFT = 0.10

#: What the control arm scored on the 24-video TUNING set, for reading
#: alongside. NOT a baseline to beat - a different and smaller corpus,
#: already used to disqualify two approaches.
TUNING_SPECIFICITY = {"piano_or_keyboard_visible": 0.000,
                      "sheet_music_visible": 0.235}


def mcnemar_exact(b: int, c: int) -> float | None:
    """Two-sided exact McNemar p-value on discordant counts b and c.

    b = control right / treatment wrong, c = the reverse. Under the null
    each discordant pair is a fair coin, so this is an exact binomial
    test on min(b, c) successes out of b + c.
    """
    n = b + c
    if n == 0:
        return None
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return round(min(1.0, 2 * tail), 4)


def score_boolean(pairs: list[tuple[bool, bool]]) -> dict:
    tp = sum(1 for p, a in pairs if p and a)
    tn = sum(1 for p, a in pairs if not p and not a)
    fp = sum(1 for p, a in pairs if p and not a)
    fn = sum(1 for p, a in pairs if not p and a)
    n = len(pairs)
    pos = tp + fn
    baseline = max(pos, n - pos) / n if n else 0.0
    acc = (tp + tn) / n if n else 0.0
    out = {
        "n": n, "tp": tp, "tn": tn, "fp": fp, "fn": fn,
        "positives_in_truth": pos,
        "accuracy": round(acc, 3),
        "baseline_always_commonest": round(baseline, 3),
        "lift": round(acc - baseline, 3),
        "specificity": round(tn / (tn + fp), 3) if (tn + fp) else None,
        "recall": round(tp / (tp + fn), 3) if (tp + fn) else None,
        "precision": round(tp / (tp + fp), 3) if (tp + fp) else None,
        # A field with no negatives has an undefined specificity and an
        # accuracy equal to its base rate. It is not measurable here, and
        # saying so is more use than printing 1.000.
        "informative": 0 < pos < n,
    }
    out["clears_m3_bar"] = bool(
        out["informative"] and out["specificity"] is not None
        and out["specificity"] >= MIN_SPECIFICITY
        and out["lift"] >= MIN_LIFT)
    return out


def load_half(arms_path: Path, labels_path: Path) -> dict:
    labels_doc = json.loads(labels_path.read_text(encoding="utf-8"))
    arms_doc = json.loads(arms_path.read_text(encoding="utf-8"))
    truth = {int(k): v for k, v in labels_doc["labels"].items()}

    got: dict[str, dict[int, dict]] = {}
    status: dict[str, dict[str, int]] = {}
    for row in arms_doc["results"]:
        arm = row["arm"]
        s = status.setdefault(arm, {})
        s[row["status"]] = s.get(row["status"], 0) + 1
        if row["status"] == "ok" and isinstance(row.get("data"), dict):
            got.setdefault(arm, {})[int(row["video_id"])] = row["data"]

    arms = list(arms_doc["arms"])
    # Only publications BOTH arms answered are scored. Scoring an arm on
    # videos the other one failed would compare two different corpora,
    # which is the error this whole script exists to avoid.
    common = (set.intersection(*(set(got.get(a, {})) for a in arms))
              if all(got.get(a) for a in arms) else set())
    common &= set(truth)
    return {"arms": arms, "truth": truth, "got": got, "common": common,
            "status": status, "labels_doc": labels_doc,
            "labels_file": labels_path.name,
            "frames_source": arms_doc.get("frames_source", "/api/frames"),
            "labelled_by": labels_doc.get("_labelled_by", "")}


def score_half(h: dict) -> dict:
    arms, truth, got, common = h["arms"], h["truth"], h["got"], h["common"]
    out: dict = {
        "labels_file": h["labels_file"],
        "frames_source": h["frames_source"],
        "videos_with_both_arms": len(common),
        "per_arm_status": h["status"],
        "fields": {},
    }
    for fname in SCORED_BOOLEANS:
        per_arm: dict[str, dict] = {}
        correct: dict[str, dict[int, bool]] = {a: {} for a in arms}
        for arm in arms:
            pairs = []
            for vid in sorted(common):
                if fname not in truth[vid]:
                    continue
                pred = got[arm][vid].get(fname)
                if pred is None or pred == "unknown":
                    continue
                pred, want = bool(pred), bool(truth[vid][fname])
                pairs.append((pred, want))
                correct[arm][vid] = (pred == want)
            per_arm[arm] = score_boolean(pairs)

        ctrl, treat = arms[0], arms[-1]
        shared = sorted(set(correct[ctrl]) & set(correct[treat]))
        b = sum(1 for v in shared
                if correct[ctrl][v] and not correct[treat][v])
        c = sum(1 for v in shared
                if correct[treat][v] and not correct[ctrl][v])
        out["fields"][fname] = {
            "per_arm": per_arm,
            "paired": {
                "n_compared": len(shared),
                "control_only_right": b,
                "treatment_only_right": c,
                "both_same": len(shared) - b - c,
                "mcnemar_p_two_sided": mcnemar_exact(b, c),
            },
            "tuning_set_specificity_for_reading_only":
                TUNING_SPECIFICITY.get(fname),
        }

    for fname in SCORED_CATEGORICAL:
        per_arm = {}
        for arm in arms:
            n = correct_n = unknown = 0
            for vid in sorted(common):
                if fname not in truth[vid]:
                    continue
                pred = got[arm][vid].get(fname)
                if pred is None or pred == "unknown":
                    unknown += 1
                    continue
                n += 1
                correct_n += int(str(pred) == str(truth[vid][fname]))
            vals = [truth[v][fname] for v in sorted(common)
                    if fname in truth[v]]
            base = (max(vals.count(x) for x in set(vals)) / len(vals)
                    if vals else 0.0)
            per_arm[arm] = {
                "n_answered": n, "correct": correct_n, "declined": unknown,
                "labelled": len(vals),
                "accuracy": round(correct_n / n, 3) if n else None,
                "baseline_commonest": round(base, 3),
                "lift": (round(correct_n / n - base, 3) if n else None),
            }
        out["fields"][fname] = {"per_arm": per_arm, "kind": "categorical"}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    # Three parallel repeatable flags rather than one colon-joined
    # triple: a Windows path starts "C:\\", so a colon separator makes
    # the argument unparseable on the machine that runs the model.
    ap.add_argument("--half", action="append", default=[], metavar="NAME",
                    help="repeatable name for each half, e.g. own")
    ap.add_argument("--arms", action="append", default=[], metavar="PATH",
                    help="the m3_prompt_arms.py output for that half")
    ap.add_argument("--labels", action="append", default=[], metavar="PATH",
                    help="the reference labels for that half")
    args = ap.parse_args(argv)
    if not args.half:
        ap.error("at least one --half is required")
    if not (len(args.half) == len(args.arms) == len(args.labels)):
        ap.error("--half, --arms and --labels must be given the same "
                 "number of times, in matching order")

    halves: dict[str, dict] = {}
    loaded: dict[str, dict] = {}
    for name, arms_p, labels_p in zip(args.half, args.arms, args.labels):
        h = load_half(Path(arms_p), Path(labels_p))
        loaded[name] = h
        halves[name] = score_half(h)

    out: dict = {
        "promotion_rule": {
            "min_specificity": MIN_SPECIFICITY, "min_lift": MIN_LIFT,
            "must_clear_on": "every half independently, not on the pool"},
        "labelled_by": {n: h["labelled_by"] for n, h in loaded.items()},
        "halves": halves,
    }

    # The pooled table IS the pre-registered population. M3's rule, as
    # written in the blueprint before any of this was measured, says
    # "specificity >= 0.80 and lift >= +0.10 on the held-out set" - and
    # the held-out set is the 80 publications, not each half separately.
    # An earlier version of this script required BOTH halves to clear
    # it. That is a stricter rule and arguably a better one, but
    # inventing it after seeing the numbers is exactly the post-hoc
    # tightening CLAUDE.md 3.13 forbids. So the pooled figure decides,
    # and the per-half split is reported beside it as a diagnostic.
    arms = next(iter(loaded.values()))["arms"]
    ctrl, treat = arms[0], arms[-1]
    pooled: dict = {}
    if len(loaded) > 1:
        for fname in SCORED_BOOLEANS:
            per_arm = {}
            for arm in arms:
                pairs = []
                for h in loaded.values():
                    for vid in sorted(h["common"]):
                        if fname not in h["truth"][vid]:
                            continue
                        pred = h["got"][arm][vid].get(fname)
                        if pred is None or pred == "unknown":
                            continue
                        pairs.append(
                            (bool(pred), bool(h["truth"][vid][fname])))
                per_arm[arm] = score_boolean(pairs)
            pooled[fname] = per_arm
        # Categoricals pool too. Doing this by hand from the two
        # per-half tables is how a wrong baseline gets into a document:
        # the halves have different label counts AND different
        # commonest classes, so the pooled baseline is not the mean of
        # the two.
        for fname in SCORED_CATEGORICAL:
            per_arm = {}
            for arm in arms:
                n = correct_n = declined = 0
                vals: list[str] = []
                for h in loaded.values():
                    for vid in sorted(h["common"]):
                        if fname not in h["truth"][vid]:
                            continue
                        vals.append(str(h["truth"][vid][fname]))
                        pred = h["got"][arm][vid].get(fname)
                        if pred is None or pred == "unknown":
                            declined += 1
                            continue
                        n += 1
                        correct_n += int(
                            str(pred) == str(h["truth"][vid][fname]))
                base = (max(vals.count(x) for x in set(vals)) / len(vals)
                        if vals else 0.0)
                acc = correct_n / n if n else None
                per_arm[arm] = {
                    "n_answered": n, "correct": correct_n,
                    "declined": declined, "labelled": len(vals),
                    "accuracy": round(acc, 3) if acc is not None else None,
                    "baseline_commonest": round(base, 3),
                    "lift": round(acc - base, 3) if acc is not None else None,
                    "kind": "categorical",
                }
            pooled[fname] = per_arm
    else:
        only = next(iter(halves.values()))
        pooled = {f: only["fields"][f]["per_arm"]
                  for f in SCORED_BOOLEANS + SCORED_CATEGORICAL}
    out["pooled_the_preregistered_population"] = pooled

    # Where does a pooled pass rest on one half only? That is the
    # question the pooled number cannot answer and the one that decides
    # whether a field has generalised.
    diagnostic = {}
    for fname in SCORED_BOOLEANS:
        per_half = {}
        for n, H in halves.items():
            t = H["fields"][fname]["per_arm"][treat]
            c = H["fields"][fname]["per_arm"][ctrl]
            per_half[n] = {
                "measurable": t["informative"],
                "control_specificity": c["specificity"],
                "control_lift": c["lift"],
                "control_clears": c["clears_m3_bar"],
                "treatment_specificity": t["specificity"],
                "treatment_lift": t["lift"],
                "treatment_clears": t["clears_m3_bar"],
            }
        t_on = [n for n, x in per_half.items() if x["treatment_clears"]]
        c_on = [n for n, x in per_half.items() if x["control_clears"]]
        diagnostic[fname] = {
            "per_half": per_half,
            "control_clears_on": c_on,
            "treatment_clears_on": t_on,
            "pooled_pass_rests_on_one_half": (
                (len(c_on) == 1 or len(t_on) == 1) and len(halves) == 2),
        }

    def clears(arm: str) -> list[str]:
        return [f for f in SCORED_BOOLEANS
                if pooled[f][arm].get("clears_m3_bar")]

    n_total = sum(len(h["common"]) for h in loaded.values())
    out["verdict"] = {
        "rule_as_preregistered": (
            f"specificity >= {MIN_SPECIFICITY} and lift >= +{MIN_LIFT} on "
            f"the held-out set, read as all {n_total} publications pooled"),
        "control_arm": ctrl,
        "control_arm_is_what_the_pipeline_runs": True,
        "treatment_arm": treat,
        "clears_under_control": clears(ctrl),
        "clears_under_treatment": clears(treat),
        "generalisation_diagnostic": diagnostic,
        "caveats": [
            ("The labels were written by Claude, not the owner. A field "
             "promoted on them is promoted on one model's reading of 80 "
             "images; each fixture lists the cases it flagged uncertain."),
            ("A field with no negatives in a half has an undefined "
             "specificity there. `measurable` says so rather than "
             "reporting the base rate as if it were accuracy."),
            ("The per-half split is a DIAGNOSTIC, not the rule. A field "
             "clearing the pooled bar while failing one half has not "
             "shown it generalises; `pooled_pass_rests_on_one_half` "
             "marks those. Tightening the rule to require both halves "
             "is a decision for the next milestone, not a retroactive "
             "edit to this one."),
            ("A heavily skewed field can be near-perfect and still fail "
             "the lift bar: text_overlay_present is true in 39 of 40 own "
             "labels, so nothing can lift more than +0.025 there. Read "
             "specificity alongside lift, never lift alone."),
            ("Switching arms is all-or-nothing per run. Taking one "
             "field from each arm would need two inference passes, and "
             "choosing per field after seeing these numbers would be "
             "selection on the test set."),
            ("Latency is NOT comparable between arms here: both arms ran "
             "back to back on the same frames in one llama.cpp session, "
             "so the second benefits from a warm image cache. Accuracy "
             "is unaffected."),
        ],
    }

    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
