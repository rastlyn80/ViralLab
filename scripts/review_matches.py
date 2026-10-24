"""Review uncertain local-master ↔ post matches.

Only the ambiguous middle band reaches here: confident matches are already
linked and weak ones were discarded. The owner never sorts the archive by
hand — they answer a short series of yes/no questions about pairs the
matcher could not settle, and can stop at any point.

Superseded for everyday use by `scripts/review_ui.py`, which plays the local
master and the published clip side by side - the thing that actually settles
a doubtful pair. This stays as the terminal fallback and for bulk actions.

    python scripts/review_matches.py --list           # see the queue
    python scripts/review_matches.py --interactive    # work through it
    python scripts/review_matches.py --accept-above 0.7
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from remote import connect, run  # noqa: E402

REMOTE = "/home/rastlyn/virallab"


def remote_query(c, sql: str) -> list:
    import shlex
    rc, out, err = run(
        c, f"cd {REMOTE} && set -a && . ./.env && set +a && "
           f".venv/bin/python sql.py --query " + shlex.quote(sql), timeout=300)
    txt = (out or err).strip()
    try:
        import ast
        return ast.literal_eval(txt) if txt.startswith("[") else []
    except (ValueError, SyntaxError):
        print(txt)
        return []


def remote_exec(c, sql: str) -> str:
    import shlex
    rc, out, err = run(
        c, f"cd {REMOTE} && set -a && . ./.env && set +a && "
           f".venv/bin/python sql.py " + shlex.quote(sql), timeout=300)
    return (out or err).strip()


PENDING_SQL = """
select l.id, v.platform, v.external_id, v.caption,
       v.posted_at::date::text,
       m.filename, m.duration_s, v.duration_s,
       l.score, l.frame_overlap, l.duration_delta_s, l.runner_up
from video_master_links l
join videos v on v.id = l.video_id
join local_masters m on m.id = l.master_id
where l.status = 'pending'
order by l.score desc
"""


def show(rows: list) -> None:
    print(f"\n  {len(rows)} pair(s) awaiting a decision\n")
    print(f"  {'#':<4} {'score':<6} {'ovlp':<5} {'Δdur':<7} "
          f"{'local file':<34} post")
    print("  " + "-" * 96)
    for i, r in enumerate(rows, 1):
        (lid, platform, ext, caption, posted, fname, mdur, vdur,
         score, ovl, ddelta, runner) = r
        cap = (caption or "")[:36].replace("\n", " ")
        flag = " [tie]" if runner and runner >= score - 0.08 else ""
        print(f"  {i:<4} {score:<6.2f} {ovl or 0:<5.2f} "
              f"{ddelta if ddelta is not None else 0:<7.1f} "
              f"{fname[:32]:<34} {platform}/{posted} {cap}{flag}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--interactive", action="store_true")
    ap.add_argument("--accept-above", type=float,
                    help="bulk-confirm every pending pair scoring above this "
                         "(ties are still left for a human)")
    ap.add_argument("--export", type=Path,
                    help="write the queue to JSON for offline review")
    ap.add_argument("--only-ids",
                    help="comma-separated link ids; restricts the queue to "
                         "those, which is how validate_matches.py hands over "
                         "a stratified spot-check sample")
    args = ap.parse_args()

    c = connect()
    try:
        rows = remote_query(c, " ".join(PENDING_SQL.split()))
        if args.only_ids:
            wanted = {int(x) for x in args.only_ids.split(",") if x.strip()}
            rows = [r for r in rows if r[0] in wanted]
            print(f"  restricted to {len(rows)} of the requested "
                  f"{len(wanted)} id(s)")
        if not rows:
            print("  nothing pending - the queue is empty")
            return 0

        if args.export:
            args.export.write_text(json.dumps([
                {"link_id": r[0], "platform": r[1], "post": r[2],
                 "posted": str(r[4]), "local_file": r[5],
                 "score": r[8], "frame_overlap": r[9],
                 "duration_delta_s": r[10]} for r in rows], indent=1),
                encoding="utf-8")
            print(f"  exported {len(rows)} -> {args.export}")
            return 0

        if args.accept_above is not None:
            ids = [r[0] for r in rows
                   if r[8] >= args.accept_above
                   and not (r[11] and r[11] >= r[8] - 0.08)]
            if not ids:
                print("  nothing qualifies")
                return 0
            remote_exec(c, "update video_master_links set status='confirmed' "
                           f"where id in ({','.join(str(i) for i in ids)})")
            print(f"  confirmed {len(ids)} pair(s) scoring >= "
                  f"{args.accept_above}; ties left pending on purpose")
            return 0

        show(rows)
        if not args.interactive:
            print("\n  --interactive to decide, --accept-above N for bulk, "
                  "--export to review offline")
            return 0

        print("\n  y = same video   n = not a match   s = skip   q = quit\n")
        confirmed, rejected = [], []
        for i, r in enumerate(rows, 1):
            (lid, platform, ext, caption, posted, fname, mdur, vdur,
             score, ovl, ddelta, runner) = r
            print(f"  [{i}/{len(rows)}] score {score:.2f} "
                  f"(overlap {ovl or 0:.2f}, Δduration {ddelta or 0:.1f}s)")
            print(f"       local : {fname}  ({mdur or 0:.1f}s)")
            print(f"       post  : {platform} {posted} "
                  f"{(caption or '')[:60]}")
            ans = input("       same video? [y/n/s/q] ").strip().lower()
            if ans == "q":
                break
            if ans == "y":
                confirmed.append(lid)
            elif ans == "n":
                rejected.append(lid)

        if confirmed:
            remote_exec(c, "update video_master_links set status='confirmed' "
                           f"where id in ({','.join(map(str, confirmed))})")
        if rejected:
            remote_exec(c, "update video_master_links set status='rejected' "
                           f"where id in ({','.join(map(str, rejected))})")
        print(f"\n  confirmed {len(confirmed)}, rejected {len(rejected)}, "
              f"{len(rows)-len(confirmed)-len(rejected)} still pending")
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
