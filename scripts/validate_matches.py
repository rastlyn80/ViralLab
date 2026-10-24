"""Validate the match links already in the database.

The 93 automatic links were produced by a scorer whose audio signal has since
been measured and found unfit for this archive: on a *different opening hook*
it scored genuine matches 0.563-0.781, below what it scored on known
negatives (up to 0.799). See docs/benchmarks/2-audio-fingerprint.md. So those
links cannot be assumed correct, and this re-checks every one of them against
the measured spectral fingerprint.

What this does and does not establish:

  * **Corroboration is not ground truth.** Agreement between two automated
    methods is evidence, not proof. The precision figures printed below are
    labelled as what they are, and a human spot-check sample is produced
    alongside them for the only real answer.
  * **A score is never a probability.** Nothing here converts a similarity
    into a likelihood of correctness.
  * **Human decisions are never overwritten.** Rows with `decided_by =
    'human'` are reported and left exactly as they are.
  * **A contradicted automatic link is demoted to `pending`, not rejected.**
    Rejecting is a human judgement; all this can say is "the evidence no
    longer supports this".

    python scripts/validate_matches.py              # report only
    python scripts/validate_matches.py --apply      # also demote + re-score
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / "probes" / "local_library"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # repo root, for shared/

from shared import audiofp  # noqa: E402
from match_local_to_posts import SPECTRAL_FLOOR, score  # noqa: E402
from dbq import execute as remote_exec, query as remote_query  # noqa: E402
from remote import connect  # noqa: E402

LINKS_SQL = """
select l.id, l.status, l.score, l.method, l.decided_by,
       m.quick_id, m.filename, v.platform, v.external_id,
       v.posted_at::date::text as posted, l.runner_up
from video_master_links l
join local_masters m on m.id = l.master_id
join videos v on v.id = l.video_id
order by l.status, l.score desc
"""




def classify(local: dict | None, post: dict | None) -> dict:
    """Re-score one link and say how well evidenced it now is."""
    if local is None or post is None:
        return {"verdict": "no_fingerprint", "reason":
                "master or publication has no stored fingerprint"}
    s, detail = score(local, post)
    method = detail["audio_method"]
    if method != "spectral":
        return {"verdict": "unverifiable", "score": round(s, 3),
                "reason": f"audio method is {method!r}, not the measured one",
                **detail}
    audio = detail["audio_similarity"]
    if audio >= SPECTRAL_FLOOR:
        return {"verdict": "corroborated", "score": round(s, 3), **detail}
    return {"verdict": "contradicted", "score": round(s, 3),
            "reason": f"spectral audio {audio:.3f} < floor {SPECTRAL_FLOOR}",
            **detail}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write re-scored evidence and demote contradicted "
                         "automatic links to pending")
    ap.add_argument("--local", type=Path,
                    default=Path("probes/local_library/fingerprints.json"))
    ap.add_argument("--posts", type=Path,
                    default=Path("probes/local_library/ig_fingerprints.json"))
    ap.add_argument("--sample", type=int, default=12,
                    help="size of the human spot-check sample")
    ap.add_argument("--out", type=Path,
                    default=Path("probes/local_library/validation.json"))
    args = ap.parse_args()

    local = json.loads(args.local.read_text(encoding="utf-8"))
    posts = json.loads(args.posts.read_text(encoding="utf-8"))
    by_quick = {v.get("quick_id", k): v for k, v in local.items()}
    by_quick.update(local)                      # keys are already quick_ids

    c = connect()
    try:
        rows = remote_query(c, LINKS_SQL)
        if not rows:
            print("  no links in the database")
            return 1
        print(f"  {len(rows)} link(s) in the database\n")

        results = []
        for r in rows:
            ext = r["external_id"]
            v = classify(by_quick.get(r["quick_id"]), posts.get(ext))
            results.append({
                "link_id": r["id"], "status": r["status"],
                "old_score": r["score"], "old_method": r["method"],
                "decided_by": r["decided_by"], "quick_id": r["quick_id"],
                "filename": r["filename"], "platform": r["platform"],
                "external_id": ext, "posted": r["posted"],
                "runner_up": r["runner_up"], **v})

        # ---- report ----------------------------------------------------
        by_status = Counter(x["status"] for x in results)
        print("=== LINKS BY STATUS ===")
        for st, n in sorted(by_status.items()):
            human = sum(1 for x in results
                        if x["status"] == st and x["decided_by"] == "human")
            print(f"  {st:<10} {n:>4}" +
                  (f"   ({human} set by a human)" if human else ""))

        print("\n=== RE-CHECK OF AUTOMATIC LINKS ===")
        auto = [x for x in results
                if x["status"] == "auto" and x["decided_by"] != "human"]
        verdicts = Counter(x["verdict"] for x in auto)
        for v in ("corroborated", "contradicted", "unverifiable",
                  "no_fingerprint"):
            if verdicts[v]:
                print(f"  {v:<16} {verdicts[v]:>4}")
        checkable = verdicts["corroborated"] + verdicts["contradicted"]
        if checkable:
            print(f"\n  Of the {checkable} automatic links that COULD be "
                  f"re-checked with the measured method,")
            print(f"  {verdicts['corroborated']} are corroborated and "
                  f"{verdicts['contradicted']} are contradicted.")
            print(f"  [ESTIMATE] agreement rate "
                  f"{verdicts['corroborated']/checkable*100:.0f}% - this is "
                  f"agreement between two\n  automated methods, NOT a "
                  f"measured precision. The spot-check sample below is the\n"
                  f"  only part of this that is ground truth.")

        contra = [x for x in auto if x["verdict"] == "contradicted"]
        if contra:
            print(f"\n=== {len(contra)} CONTRADICTED AUTOMATIC LINK(S) ===")
            print(f"  {'id':<6} {'was':<6} {'audio':<7} {'ovlp':<6} "
                  f"{'local file':<34} post")
            for x in sorted(contra, key=lambda y: -(y["old_score"] or 0)):
                print(f"  {x['link_id']:<6} {x['old_score'] or 0:<6.2f} "
                      f"{x.get('audio_similarity', 0):<7.3f} "
                      f"{x.get('frame_overlap', 0):<6.2f} "
                      f"{x['filename'][:32]:<34} "
                      f"{x['platform']}/{x['posted']}")

        # ---- the portrait series, called out by name -------------------
        portraits = [x for x in results
                     if "variation" in x["filename"].lower()
                     or "portrait" in x["filename"].lower()]
        print(f"\n=== PORTRAIT SERIES ({len(portraits)} link(s)) ===")
        if portraits:
            print("  This series is visually identical and differs only in "
                  "the music; it is\n  what produced ~10 confident false "
                  "links in the first pass.")
            for x in sorted(portraits, key=lambda y: y["filename"]):
                print(f"  {x['status']:<9} {x['verdict']:<14} "
                      f"audio {x.get('audio_similarity', 0):<6.3f} "
                      f"{x['filename'][:34]:<36} {x['platform']}/{x['posted']}")
        else:
            print("  none linked - consistent with the series having been "
                  "cleared of false matches.")

        # ---- human spot-check sample, stratified -----------------------
        random.seed(20261024)
        bands = {
            "high (>= 0.90)": [x for x in auto if (x["old_score"] or 0) >= 0.90],
            "borderline (0.80-0.90)": [x for x in auto
                                       if 0.80 <= (x["old_score"] or 0) < 0.90],
            "ambiguous (near-tie runner-up)":
                [x for x in results
                 if x["runner_up"] and x["old_score"]
                 and x["runner_up"] >= x["old_score"] - 0.08],
        }
        per = max(1, args.sample // len(bands))
        sample = []
        print("\n=== HUMAN SPOT-CHECK SAMPLE ===")
        for band, pool in bands.items():
            pick = random.sample(pool, min(per, len(pool)))
            print(f"  {band}: {len(pool)} link(s), sampling {len(pick)}")
            for x in pick:
                x = dict(x, band=band)
                sample.append(x)
        print("\n  review these with:  python scripts/review_matches.py "
              "--interactive --only-ids " +
              ",".join(str(x["link_id"]) for x in sample))

        args.out.write_text(json.dumps(
            {"links": results, "spot_check_sample": sample}, indent=1),
            encoding="utf-8")
        print(f"\n  -> {args.out}")

        # ---- apply ------------------------------------------------------
        if not args.apply:
            print("\n  (report only; --apply to write evidence and demote "
                  "contradicted links)")
            return 0

        updates = 0
        for x in results:
            if x["verdict"] in ("no_fingerprint", ):
                continue
            sets = [
                f"audio_similarity = {x.get('audio_similarity', 0):.4f}",
                f"audio_method = '{x.get('audio_method', 'none')}'",
                "evidence = " + sql_json({
                    k: x.get(k) for k in
                    ("verdict", "score", "frame_overlap", "audio_similarity",
                     "audio_method", "audio_offset_s", "duration_delta_s",
                     "same_cut", "reason")}),
            ]
            if x.get("audio_offset_s") is not None:
                sets.append(f"audio_offset_s = {x['audio_offset_s']:.3f}")
            # Demote only matcher-set automatic links, and only to pending.
            if (x["status"] == "auto" and x["decided_by"] != "human"
                    and x["verdict"] == "contradicted"):
                sets.append("status = 'pending'")
                sets.append("decided_by = 'matcher'")
                sets.append("notes = 'demoted by validate_matches.py: "
                            "spectral audio contradicts the original link'")
            remote_exec(c, f"update video_master_links set "
                           f"{', '.join(sets)} where id = {x['link_id']}")
            updates += 1
        print(f"\n  wrote evidence to {updates} link(s); "
              f"{len(contra)} demoted to pending")
        print("  human decisions left untouched: " +
              str(sum(1 for x in results if x["decided_by"] == "human")))
        return 0
    finally:
        c.close()


def sql_json(d: dict) -> str:
    return "'" + json.dumps(d).replace("'", "''") + "'::jsonb"


if __name__ == "__main__":
    raise SystemExit(main())
