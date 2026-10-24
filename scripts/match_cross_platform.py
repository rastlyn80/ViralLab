"""Propose TikTok <-> Instagram pairs for the same video.

The owner cross-posts ~99% of videos, so this is the normal case, not an edge
case. What makes it hard is that the two sides are not symmetric:

  * **Instagram** media is downloaded, so an Instagram publication has a
    spectral audio fingerprint and is already matched to a local master -
    strong, content-level evidence.
  * **TikTok** returns no media file. There is no TikTok audio and no TikTok
    frames. The cover image was implemented, hashed for all 234 publications,
    and then **measured and rejected**: the best cover-vs-master Hamming
    distance had median 16/64 and beat its runner-up by >=6 on only 1 of 234
    posts, and re-sampling masters at 2 fps did not help (3/14 improved).
    See docs/benchmarks/2-cross-platform-matching.md.

So the only signals that survive are metadata: duration, publication time,
and caption text. None of them can establish that two publications are the
same video - thousands of piano videos share a duration - so this script
**never confirms anything**. It writes `publication_links` rows with status
'proposed', and `publication_links` deliberately has no 'auto' status at all.

What makes the proposals worth trusting is structure rather than any one
signal: because almost everything is cross-posted, the true answer is close
to a one-to-one pairing. A pair is proposed only when each side is the
other's best candidate (mutual best), and the tier records which independent
signals actually agreed.

    python scripts/match_cross_platform.py             # report
    python scripts/match_cross_platform.py --apply     # write proposals
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dbq import execute as db_execute, query as db_query  # noqa: E402
from remote import connect  # noqa: E402

#: TikTok reports duration as whole seconds while Instagram's comes from the
#: media file, so up to ~1 s of difference is rounding, not evidence.
DUR_EXACT = 1.1
DUR_NEAR = 2.5
DUR_LOOSE = 5.0

#: Same-day is the common case. The band has to be tight precisely BECAUSE
#: the owner posts near-daily: if "within a day" counted as a perfect match,
#: yesterday's post would be almost as good a candidate as today's, and the
#: margin over the runner-up - the thing that makes a mutual-best pairing
#: trustworthy - would collapse.
DAY_EXACT = 0
DAY_NEAR = 2
DAY_LOOSE = 14

#: A proposal needs at least this total and at least two agreeing signals.
MIN_SCORE = 0.55
MIN_SIGNALS = 2

STOP = {"the", "and", "for", "with", "that", "this", "from", "have", "what",
        "when", "your", "just", "about", "they", "were", "been", "more",
        "than", "into", "over", "also", "some", "most", "very", "like",
        "piano", "music"}


def tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z]{4,}", (text or "").lower())
    return {w for w in words if w not in STOP}


def hashtags(text: str) -> set[str]:
    return {h.lower() for h in re.findall(r"#(\w+)", text or "")}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def parse_day(s: str | None) -> datetime | None:
    """Calendar day of a publication, from an ISO timestamp."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def pair_score(t: dict, i: dict) -> dict:
    """Per-signal agreement for one candidate pair.

    Each signal is scored independently and reported separately, so a
    proposal can be read as "these three things agreed" rather than as a
    single opaque number.
    """
    sig = {}

    dd = (abs(t["duration"] - i["duration"])
          if t["duration"] and i["duration"] else None)
    if dd is None:
        sig["duration"] = None
    elif dd <= DUR_EXACT:
        sig["duration"] = 1.0
    elif dd <= DUR_NEAR:
        sig["duration"] = 0.6
    elif dd <= DUR_LOOSE:
        sig["duration"] = 0.2
    else:
        sig["duration"] = 0.0

    td, idt = t["day"], i["day"]
    gap_days = abs((td - idt).days) if td and idt else None
    if gap_days is None:
        sig["date"] = None
    elif gap_days <= DAY_EXACT:
        sig["date"] = 1.0
    elif gap_days == 1:
        sig["date"] = 0.75
    elif gap_days <= DAY_NEAR:
        sig["date"] = 0.55
    elif gap_days <= DAY_LOOSE:
        sig["date"] = 0.25
    else:
        sig["date"] = 0.0

    tj = jaccard(t["tokens"], i["tokens"])
    hj = jaccard(t["tags"], i["tags"])
    # Captions are rewritten per platform, so overlap is partial by design;
    # a high value is strong evidence, a low one is not evidence against.
    sig["caption"] = max(tj, hj) if (t["tokens"] or t["tags"]) else None

    weights = {"duration": 0.45, "date": 0.35, "caption": 0.20}
    total = avail = 0.0
    for k, w in weights.items():
        if sig[k] is None:
            continue
        total += w * sig[k]
        avail += w
    score = total / avail if avail else 0.0

    agreeing = sum(1 for k in ("duration", "date", "caption")
                   if (sig[k] or 0) >= 0.6)
    return {"score": round(score, 4), "signals": sig,
            "agreeing": agreeing, "duration_delta_s": dd,
            "day_gap": gap_days, "token_jaccard": round(tj, 3),
            "hashtag_jaccard": round(hj, 3)}


def tier(ev: dict) -> str:
    """Honest labels. 'strong' still means "worth a human confirming",
    never "established"."""
    s, n = ev["score"], ev["agreeing"]
    if n >= 3 and s >= 0.85:
        return "strong"
    if n >= 2 and s >= 0.70:
        return "moderate"
    return "weak"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out", type=Path,
                    default=Path("probes/local_library/cross_platform.json"))
    args = ap.parse_args()

    c = connect()
    try:
        tk_rows = db_query(
            c, "select id, external_id, posted_at, duration_s, caption "
               "from videos where platform='tiktok' order by posted_at")
        ig_rows = db_query(
            c, "select v.id, v.external_id, v.posted_at, v.duration_s, "
               "v.caption, m.filename, p.title, l.status "
               "from videos v "
               "left join video_master_links l on l.video_id = v.id "
               "left join local_masters m on m.id = l.master_id "
               "left join performances p on p.id = m.performance_id "
               "where v.platform='instagram' order by v.posted_at")

        tk = [{"id": r["id"], "ext": r["external_id"],
               "day": parse_day(r["posted_at"]), "posted": r["posted_at"],
               "duration": r["duration_s"],
               "tokens": tokens(r["caption"]), "tags": hashtags(r["caption"]),
               "caption": r["caption"]} for r in tk_rows]
        # One row per Instagram publication; the join can duplicate when a
        # publication has several master links.
        seen: dict[int, dict] = {}
        for r in ig_rows:
            vid = r["id"]
            if vid in seen:
                if (r["status"] in ("auto", "confirmed")
                        and not seen[vid]["master"]):
                    seen[vid]["master"] = r["filename"]
                    seen[vid]["performance"] = r["title"]
                continue
            seen[vid] = {"id": vid, "ext": r["external_id"],
                         "day": parse_day(r["posted_at"]),
                         "posted": r["posted_at"],
                         "duration": r["duration_s"],
                         "tokens": tokens(r["caption"]),
                         "tags": hashtags(r["caption"]),
                         "caption": r["caption"], "master": r["filename"],
                         "performance": r["title"],
                         "link_status": r["status"]}
        ig = list(seen.values())

        print(f"  {len(tk)} TikTok x {len(ig)} Instagram publication(s)")
        print(f"  TikTok with duration : "
              f"{sum(1 for t in tk if t['duration'])}/{len(tk)}")
        print(f"  Instagram with duration: "
              f"{sum(1 for i in ig if i['duration'])}/{len(ig)}")
        print(f"  Instagram already tied to a local master: "
              f"{sum(1 for i in ig if i['master'])}/{len(ig)}")

        # ---- score every pair, keep each side's ranked candidates -----
        best_for_tk: dict[int, list] = {}
        best_for_ig: dict[int, list] = {}
        for t in tk:
            cands = []
            for i in ig:
                ev = pair_score(t, i)
                if ev["score"] >= 0.40:
                    cands.append((ev["score"], i["id"], ev))
            cands.sort(reverse=True, key=lambda x: x[0])
            best_for_tk[t["id"]] = cands[:4]
            for sc, iid, ev in cands:
                best_for_ig.setdefault(iid, []).append((sc, t["id"], ev))
        for iid in best_for_ig:
            best_for_ig[iid].sort(reverse=True, key=lambda x: x[0])

        by_tk = {t["id"]: t for t in tk}
        by_ig = {i["id"]: i for i in ig}

        proposals, rejected_weak, not_mutual = [], [], []
        for tid, cands in best_for_tk.items():
            if not cands:
                continue
            sc, iid, ev = cands[0]
            # Mutual best: the Instagram side must also rank this TikTok
            # publication first. With ~99% cross-posting the truth is close
            # to a bijection, so a one-sided preference is usually two posts
            # competing for the same partner - exactly the case where a
            # greedy pick goes wrong.
            if not best_for_ig.get(iid) or best_for_ig[iid][0][1] != tid:
                not_mutual.append((tid, iid, ev))
                continue
            runner = cands[1][0] if len(cands) > 1 else 0.0
            row = {
                "tiktok_id": tid, "tiktok_ext": by_tk[tid]["ext"],
                "tiktok_posted": by_tk[tid]["posted"],
                "tiktok_duration": by_tk[tid]["duration"],
                "instagram_id": iid, "instagram_ext": by_ig[iid]["ext"],
                "instagram_posted": by_ig[iid]["posted"],
                "instagram_duration": by_ig[iid]["duration"],
                "instagram_master": by_ig[iid]["master"],
                "instagram_performance": by_ig[iid]["performance"],
                "score": sc, "runner_up": round(runner, 4),
                "margin": round(sc - runner, 4),
                "tier": tier(ev), **ev}
            if ev["score"] >= MIN_SCORE and ev["agreeing"] >= MIN_SIGNALS:
                proposals.append(row)
            else:
                rejected_weak.append(row)

        tiers = Counter(p["tier"] for p in proposals)
        print(f"\n=== PROPOSED PAIRS: {len(proposals)} ===")
        for name in ("strong", "moderate", "weak"):
            if tiers[name]:
                print(f"  {name:<10} {tiers[name]:>4}")
        print(f"\n  not mutual best (two posts wanted the same partner): "
              f"{len(not_mutual)}")
        print(f"  below the evidence floor (score < {MIN_SCORE} or "
              f"< {MIN_SIGNALS} agreeing signals): {len(rejected_weak)}")
        unpaired_tk = [t for t in tk
                       if t["id"] not in {p["tiktok_id"] for p in proposals}]
        unpaired_ig = [i for i in ig
                       if i["id"] not in {p["instagram_id"]
                                          for p in proposals}]
        print(f"  TikTok publications with no proposal   : "
              f"{len(unpaired_tk)}/{len(tk)}")
        print(f"  Instagram publications with no proposal: "
              f"{len(unpaired_ig)}/{len(ig)}")

        margins = sorted(q["margin"] for q in proposals)
        if margins:
            print(f"\n  margin over the runner-up: min {margins[0]:.2f}, "
                  f"median {margins[len(margins)//2]:.2f}, "
                  f"max {margins[-1]:.2f}")
            thin = [q for q in proposals if q["margin"] < 0.10]
            print(f"  {len(thin)} proposal(s) beat their runner-up by < 0.10 - "
                  f"the ones most worth a human eye")
        print(f"\n  {'tier':<10} {'score':<6} {'marg':<6} {'dur':<9} "
              f"{'days':<5} {'cap':<5} TikTok -> Instagram")
        for p in sorted(proposals, key=lambda r: -r["score"])[:30]:
            print(f"  {p['tier']:<10} {p['score']:<6.2f} {p['margin']:<6.2f} "
                  f"{(p['duration_delta_s'] or 0):<9.2f} "
                  f"{str(p['day_gap']):<5} "
                  f"{max(p['token_jaccard'], p['hashtag_jaccard']):<5.2f} "
                  f"{p['tiktok_posted'][:10]} -> {p['instagram_posted'][:10]}"
                  f"  {(p['instagram_master'] or '-')[:26]}")

        args.out.write_text(json.dumps(
            {"proposals": proposals, "rejected_weak": rejected_weak,
             "not_mutual_best": [{"tiktok_id": a, "instagram_id": b,
                                  "score": e["score"]}
                                 for a, b, e in not_mutual],
             "unpaired_tiktok": [t["ext"] for t in unpaired_tk],
             "unpaired_instagram": [i["ext"] for i in unpaired_ig]},
            indent=1), encoding="utf-8")
        print(f"\n  -> {args.out}")

        print("\n  NOTE: every row above is a PROPOSAL. Metadata agreement "
              "cannot establish that\n  two publications are the same video, "
              "so none of these is written as confirmed\n  and none will be "
              "without a human decision.")

        if not args.apply:
            print("\n  (report only; --apply to write proposals)")
            return 0

        written = skipped_human = 0
        for p in proposals:
            a, b = sorted((p["tiktok_id"], p["instagram_id"]))
            ev = json.dumps({
                "signals": p["signals"], "agreeing": p["agreeing"],
                "duration_delta_s": p["duration_delta_s"],
                "day_gap": p["day_gap"],
                "token_jaccard": p["token_jaccard"],
                "hashtag_jaccard": p["hashtag_jaccard"],
                "tier": p["tier"], "margin": p["margin"],
                "runner_up": p["runner_up"],
                "instagram_master": p["instagram_master"],
                "instagram_performance": p["instagram_performance"],
                "cover_image_signal": "unavailable: measured and rejected, "
                                      "see docs/benchmarks/"
                                      "2-cross-platform-matching.md",
            }).replace("'", "''")
            db_execute(c, f"""
                insert into publication_links
                  (video_a_id, video_b_id, relationship_type, status, score,
                   evidence, method, decided_by, decided_at)
                values ({a}, {b}, 'same_variant', 'proposed', {p['score']},
                        '{ev}'::jsonb, 'metadata-v1', 'matcher', now())
                on conflict (video_a_id, video_b_id) do update
                  set score = excluded.score,
                      evidence = excluded.evidence,
                      method = excluded.method,
                      decided_at = now()
                  where publication_links.decided_by is distinct from 'human'
            """.strip())
            written += 1
        print(f"\n  wrote {written} proposal(s) as status='proposed'")
        for row in db_query(
                c, "select status, relationship_type, count(*) as n "
                   "from publication_links group by 1,2 order by 1,2"):
            print(f"    {row['status']:<10} {row['relationship_type']:<14} "
                  f"{row['n']:>4}")
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
