"""Can a threshold clear the pre-registered bar?

Separation says the detector ranks videos sensibly. Using it
means committing to a cut-off, and the bar in
`docs/benchmarks/7b-holdout-protocol.md` is specificity >= 0.80
with lift >= +0.10 over answering the majority class every time.

**Every number here is in-sample.** The threshold is chosen on
the same answers it is then scored against, which flatters it —
the protocol tunes on one cohort and tests on the other, and
there is only part of one cohort so far. Reported anyway,
clearly marked, because "the best case is still not good enough"
is a conclusion that can be drawn early and "it passed" is not.
"""
import json
import os
import pathlib
import sys

sys.path.insert(0, "/home/rastlyn/virallab")
from sqlalchemy import create_engine, text

scores = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
by_video = {int(k): v for k, v in (scores.get("scores") or {}).items()}

eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
with eng.begin() as c:
    labels = {}
    for r in c.execute(text(
            "select video_id, field, owner_label from label_reviews "
            "where cohort = 'holdout'")):
        labels.setdefault(r.field, {})[int(r.video_id)] = r.owner_label

BAR_SPEC, BAR_LIFT = 0.80, 0.10

for field, answers in sorted(labels.items()):
    pairs = []
    for vid, said in answers.items():
        s = by_video.get(vid, {}).get(field)
        if s is None or said not in ("yes", "no"):
            continue
        pairs.append((float(s), said == "yes"))
    if not pairs:
        print(f"\n{field}\n  no detector score for this question")
        continue
    npos = sum(1 for _s, y in pairs if y)
    nneg = len(pairs) - npos
    if not npos or not nneg:
        print(f"\n{field}\n  needs both a yes and a no")
        continue

    # Answering the majority class every time: the thing any
    # detector has to beat before it is worth running.
    majority = max(npos, nneg) / len(pairs)

    best = None
    for cut, _ in sorted(pairs):
        tp = sum(1 for s, y in pairs if y and s >= cut)
        tn = sum(1 for s, y in pairs if not y and s < cut)
        spec = tn / nneg
        rec = tp / npos
        acc = (tp + tn) / len(pairs)
        lift = acc - majority
        if spec < BAR_SPEC:
            continue
        if best is None or lift > best["lift"]:
            best = {"cut": cut, "spec": spec, "recall": rec,
                    "accuracy": acc, "lift": lift}

    print(f"\n{field}")
    print(f"  {npos} yes, {nneg} no "
          f"(always answering '{'yes' if npos >= nneg else 'no'}' "
          f"is right {majority:.0%} of the time)")
    if best is None:
        print(f"  NO threshold reaches specificity {BAR_SPEC:.2f}. "
              f"The bar is unreachable on this data, not merely "
              f"unmet.")
        continue
    verdict = ("CLEARS BOTH BARS, in-sample"
               if best["lift"] >= BAR_LIFT else
               f"specificity ok, lift {best['lift']:+.2f} is under "
               f"the {BAR_LIFT:+.2f} bar")
    print(f"  best threshold {best['cut']:.3f}: "
          f"specificity {best['spec']:.2f}, recall {best['recall']:.2f}, "
          f"accuracy {best['accuracy']:.2f}, lift {best['lift']:+.2f}")
    print(f"  -> {verdict}")

print("\nEvery figure above is tuned and scored on the same "
      "answers, which flatters it. A pass here is not a pass.")
