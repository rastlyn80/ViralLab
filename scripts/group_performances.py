"""Group local masters into performances.

A *performance* is one reading of one work; a *variant* is one export of it.
The owner's archive is full of variants - `Billie Eilish - Improved` /
`- with Intro` / `- Test 3`, or `Pop Etude 1` / `- Manychat` / `- TikTok` -
because ~99% of videos are cross-posted and the hook, overlays and trim are
tailored per platform.

Why this matters beyond tidiness: without it the matcher reports these as
*ambiguous*, because a published post scores ~1.0 against three different
files and no single one can be picked. That framing is wrong. The post is
unambiguously **that performance**; only which export was used is open. Once
variants are grouped, the matcher can say so, and the owner is asked a far
easier question - or no question at all, when the variant does not matter.

Grouping is by **audio**, not by filename. Filenames are a hint and are used
only to label a group, never to form one: two files can share a stem and be
different takes, and two takes of one edit can be named inconsistently.

    python scripts/group_performances.py                  # report
    python scripts/group_performances.py --apply           # write to the DB
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / "probes" / "local_library"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # repo root, for shared/

from shared import audiofp  # noqa: E402
from dbq import execute as remote_exec, query as remote_query  # noqa: E402
from remote import connect  # noqa: E402

#: Audio agreement at which two files are the same performance. Set to the
#: same measured floor the matcher uses (docs/benchmarks/2-audio-fingerprint
#: .md: worst known positive 0.862, worst known negative 0.425).
PERFORMANCE_FLOOR = 0.70

#: Suffixes the owner appends to variants of one export. Used only to NAME a
#: group, after audio has already decided its membership.
VARIANT_SUFFIX = re.compile(
    r"\s*[-_]?\s*("
    r"improved|with intro|intro|test\s*\d*|v\d+|final|short|shorter|longer|"
    r"tiktok|reel|reels|insta|instagram|manychat|hook|\d+"
    r")\s*$", re.IGNORECASE)


def base_label(name: str) -> str:
    stem = Path(name).stem
    prev = None
    while prev != stem:
        prev = stem
        stem = VARIANT_SUFFIX.sub("", stem).strip(" -_")
    return stem or Path(name).stem


class Union:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def join(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra




def sql_str(s: str) -> str:
    return "'" + (s or "").replace("'", "''") + "'"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", type=Path,
                    default=Path("probes/local_library/fingerprints.json"))
    ap.add_argument("--apply", action="store_true",
                    help="create performances and assign masters in the DB")
    ap.add_argument("--out", type=Path,
                    default=Path("probes/local_library/performances.json"))
    args = ap.parse_args()

    local = json.loads(args.local.read_text(encoding="utf-8"))
    have = {k: v for k, v in local.items() if v.get("audiofp")}
    print(f"  {len(local)} masters, {len(have)} with a spectral fingerprint")
    skipped = [v["name"] for v in local.values() if not v.get("audiofp")]
    if skipped:
        print(f"  {len(skipped)} without one, left ungrouped: "
              f"{', '.join(skipped[:5])}")

    keys = sorted(have)
    u = Union()
    for k in keys:
        u.find(k)

    pairs = edges = 0
    linked: dict[tuple[str, str], dict] = {}
    for i, a in enumerate(keys):
        fa = have[a]["audiofp"]
        for b in keys[i + 1:]:
            fb = have[b]["audiofp"]
            pairs += 1
            # No screening here, deliberately. Grouping has no independent
            # visual signal to fall back on, so a wrong veto is silent data
            # loss - and one already happened: a 9 s excerpt of a 37 s
            # master scored 0.191 on the screen against 1.000 at full
            # fidelity, which cost 30 genuine variant edges.
            #
            # The screen was never worth that risk at this scale. Measured:
            # 3.7 ms screened vs 7.6 ms full, so across 31k master pairs it
            # saves about two minutes. Correctness is worth two minutes.
            full = audiofp.compare(fa, fb)
            if full.get("comparable") and full["score"] >= PERFORMANCE_FLOOR:
                u.join(a, b)
                linked[(a, b)] = full
                edges += 1
    print(f"  compared {pairs:,} master pairs, {edges} above "
          f"{PERFORMANCE_FLOOR}")

    groups: dict[str, list[str]] = defaultdict(list)
    for k in keys:
        groups[u.find(k)].append(k)

    out = []
    for root, members in sorted(groups.items(),
                                key=lambda kv: -len(kv[1])):
        names = [have[m]["name"] for m in members]
        # Label from the base label shared by most members, breaking ties
        # toward the LONGEST file: when a group is one long recording plus
        # excerpts of it, the long file is the performance and the excerpts
        # are named after the clip they became.
        labels = [base_label(n) for n in names]
        longest = max(members, key=lambda m: have[m].get("duration_s") or 0)
        label = max(set(labels),
                    key=lambda L: (labels.count(L),
                                   L == base_label(have[longest]["name"])))

        # Cohesion: the weakest link INSIDE the group. Membership is
        # transitive on purpose - one long improvisation can yield several
        # excerpts that do not overlap each other, and they are still the
        # same performance, reachable only through the long file. The cost of
        # that is chaining, so the weakest link is reported rather than
        # hidden: a group held together by one marginal edge is visible as
        # such instead of looking as solid as a fully-connected one.
        inside = [v["score"] for (x, y), v in linked.items()
                  if x in members and y in members]
        cohesion = min(inside) if inside else None
        out.append({"label": label, "members": members, "names": names,
                    "size": len(members),
                    "cohesion": round(cohesion, 3) if inside else None,
                    "edges": len(inside)})

    multi = [g for g in out if g["size"] > 1]
    print(f"\n=== {len(out)} PERFORMANCE(S) from {len(have)} master(s) ===")
    print(f"  {len(multi)} have more than one variant, covering "
          f"{sum(g['size'] for g in multi)} files")
    print(f"  {len(out) - len(multi)} are a single file\n")
    print(f"  {'variants':<9} {'cohesion':<9} {'edges':<6} performance")
    for g in multi:
        # A group of n files held together by fewer than n-1 strong edges
        # cannot happen; one held together by exactly n-1 is a chain.
        chain = " <- chain, weakest link only" if g["edges"] == g["size"] - 1 \
            and g["size"] > 2 else ""
        print(f"  {g['size']:<9} {g['cohesion'] or 0:<9.3f} "
              f"{g['edges']:<6} {g['label'][:34]}{chain}")
        for n in sorted(g["names"]):
            print(f"            - {n}")

    weak = [g for g in multi if (g["cohesion"] or 1) < 0.80]
    if weak:
        print(f"\n  {len(weak)} group(s) have a weakest link below 0.80 and "
              f"are worth a glance:")
        for g in weak:
            print(f"    {g['cohesion']:.3f}  {g['label'][:40]} "
                  f"({g['size']} files)")

    args.out.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\n  -> {args.out}")

    if not args.apply:
        print("\n  (report only; --apply to write performances to the DB)")
        return 0

    c = connect()
    try:
        rows = remote_query(
            c, "select quick_id, id from local_masters")
        master_id = {r["quick_id"]: r["id"] for r in rows}
        print(f"\n  {len(master_id)} master(s) present in the database")

        created = assigned = 0
        for g in out:
            present = [m for m in g["members"] if m in master_id]
            if not present:
                continue
            # A single-file group gets a performance too: the layer should
            # exist for every master, so later analysis never has to special
            # -case "no performance".
            label = sql_str(g["label"])
            note = sql_str(
                f"grouped by spectral audio at floor {PERFORMANCE_FLOOR}; "
                f"{g['edges']} internal edge(s), weakest "
                f"{g['cohesion'] if g['cohesion'] is not None else 'n/a'}")
            res = remote_query(
                c, f"insert into performances (title, status, notes) "
                   f"values ({label}, 'inferred', {note}) returning id")
            if not res:
                print(f"  could not create performance for {g['label']!r}")
                continue
            pid = res[0]["id"]
            created += 1
            ids = ",".join(str(master_id[m]) for m in present)
            remote_exec(c, f"update local_masters set performance_id = {pid} "
                           f"where id in ({ids})")
            assigned += len(present)
        print(f"  created {created} performance(s), "
              f"assigned {assigned} master(s)")
        print("  status is 'inferred' - none of these is a confirmed "
              "grouping until the owner says so")
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
