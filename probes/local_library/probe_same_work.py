"""Can the audio fingerprint tell two DIFFERENT takes of the SAME work apart?

This is the open question left by `docs/benchmarks/2-audio-fingerprint.md`:
its known negatives are all unrelated performances, so nothing measured
whether two separate readings of the same piece by the same performer -
same notes, same instrument, same room, different take - stay below the
matching floor.

Weak ground truth, derived without looking at audio: two files whose names
refer to the same work are candidates. If the audio grouping already put
them in one performance they are variants of one export and are excluded;
what remains are same-work pairs that audio says are NOT the same recording,
which is exactly the population to measure.

The labels are filenames, so this is `[SECONDARY]` evidence and the report
says so. It is still far better than having no measurement at all.

    python probes/local_library/probe_same_work.py
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from shared import audiofp  # noqa: E402

#: Works the owner's archive actually contains, as name fragments. Matching
#: is on the fragment, so "Ballade 4" and "Chopin Ballade No 4" both land in
#: the same bucket.
WORK_PATTERNS = [
    "ballade", "clair de lune", "la valse", "winterwind", "winderwind",
    "fur elise", "moonlight", "bach fugue", "rach 2", "rach 3",
    "chopin concerto", "tchaikovsky concerto", "liszt", "liebestod",
    "carnaval", "janacek", "martinu", "debussy", "beethoven fifth",
    "chopin polonaise", "pop etude", "improv",
]

VARIANT_SUFFIX = re.compile(
    r"\s*[-_]?\s*("
    r"improved|with intro|intro|test\s*\d*|v\d+|final|short|shorter|longer|"
    r"tiktok|reel|reels|insta|instagram|manychat|hook|raw|colour|color|\d+"
    r")\s*$", re.IGNORECASE)


def work_of(name: str) -> str | None:
    low = name.lower()
    hits = [w for w in WORK_PATTERNS if w in low]
    return max(hits, key=len) if hits else None


def main() -> int:
    fp_path = Path("probes/local_library/fingerprints.json")
    perf_path = Path("probes/local_library/performances.json")
    if not fp_path.exists():
        print("  no fingerprints.json; run the scan first")
        return 1
    local = json.loads(fp_path.read_text(encoding="utf-8"))
    have = {k: v for k, v in local.items() if v.get("audiofp")}

    # Which performance group each master is in, so same-export variants can
    # be excluded.
    group_of: dict[str, str] = {}
    if perf_path.exists():
        for g in json.loads(perf_path.read_text(encoding="utf-8")):
            for m in g["members"]:
                group_of[m] = g["label"]

    buckets: dict[str, list[str]] = defaultdict(list)
    for k, v in have.items():
        w = work_of(v["name"])
        if w:
            buckets[w].append(k)

    print(f"  {len(have)} masters with a fingerprint")
    print(f"  {len(buckets)} work bucket(s) with at least one file\n")

    rows = []
    for work, members in sorted(buckets.items()):
        if len(members) < 2:
            continue
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                same_group = (group_of.get(a) is not None
                              and group_of.get(a) == group_of.get(b))
                r = audiofp.compare(have[a]["audiofp"], have[b]["audiofp"])
                rows.append({
                    "work": work,
                    "a": have[a]["name"], "b": have[b]["name"],
                    "same_performance_group": same_group,
                    "score": r["score"], "coverage": r["coverage"],
                    "aligned_frames": r["aligned_frames"],
                    "comparable": r.get("comparable", False),
                })

    if not rows:
        print("  no work has two or more files; nothing to measure")
        return 0

    variants = [r for r in rows if r["same_performance_group"]]
    takes = [r for r in rows if not r["same_performance_group"]]

    print("=== SAME WORK, SAME PERFORMANCE GROUP (expected HIGH) ===")
    print(f"  {len(variants)} pair(s) - these are variants of one export")
    for r in sorted(variants, key=lambda x: x["score"])[:8]:
        print(f"  {r['score']:<7.3f} cov {r['coverage']:<5.2f} "
              f"{r['a'][:30]:<32} vs {r['b'][:30]}")

    print(f"\n=== SAME WORK, DIFFERENT PERFORMANCE GROUP (expected LOW) ===")
    print(f"  {len(takes)} pair(s) - candidate different takes of one work")
    print(f"  {'score':<7} {'cov':<5} {'frames':<7} pair")
    for r in sorted(takes, key=lambda x: -x["score"])[:20]:
        print(f"  {r['score']:<7.3f} {r['coverage']:<5.2f} "
              f"{r['aligned_frames']:<7} {r['a'][:30]:<32} vs "
              f"{r['b'][:30]}")

    floor = 0.70
    print("\n=== VERDICT ===")
    if takes:
        scores = sorted(r["score"] for r in takes)
        over = [r for r in takes if r["score"] >= floor]
        print(f"  same-work different-take scores: min {scores[0]:.3f}, "
              f"median {scores[len(scores)//2]:.3f}, max {scores[-1]:.3f}")
        print(f"  pairs at or above the {floor} matching floor: "
              f"{len(over)}/{len(takes)}")
        if over:
            print("  ^ these would be treated as the same recording. Each is "
                  "either a genuine\n    duplicate export the grouping "
                  "missed, or a false positive worth a look:")
            for r in over:
                print(f"      {r['score']:.3f}  {r['a'][:34]} vs "
                      f"{r['b'][:34]}")
        else:
            print("  -> no same-work pair reaches the floor, so two takes of "
                  "one piece are\n     not confused with one another on this "
                  "evidence.")
    if variants:
        vs = sorted(r["score"] for r in variants)
        print(f"  same-export variants: min {vs[0]:.3f}, "
              f"median {vs[len(vs)//2]:.3f}")

    print("\n  [SECONDARY] Ground truth here is filenames, not listening. A "
          "pair labelled\n  same-work could be mislabelled, and a work the "
          "patterns do not name is\n  invisible to this probe.")

    out = Path("probes/local_library/same_work_probe.json")
    out.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(f"\n  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
