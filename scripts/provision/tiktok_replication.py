"""Does the Instagram density finding reproduce on TikTok? Runs ON Ubuntu.

A replication test, written so it can fail. The Instagram result (n=258,
`docs/benchmarks/6c-visual-density.md` §A.4–A.6) is the prediction; TikTok
is the held-out platform. Different algorithm, different audience, and —
per the owner — never boosted, which is what makes it the control for the
confound §A.3 could not resolve.

**Stated before running, so this is a test rather than a search:**

The Instagram finding is that opening visual density goes with better
per-viewer outcomes. On the four outcomes TikTok can supply, that predicts
**positive** correlations between `edge_density`,
`background_edge_density`, `text_region_score` and both `shares_per_view`
and `likes_per_view`. Instagram's effect sizes, ad-controlled, were
+0.162 to +0.229 for shares and +0.142 to +0.144 for likes.

Replication means: **same sign, comparable magnitude, surviving the same
duration and age controls.** A null is a real result and is reported as
one. A sign flip would be strong evidence the Instagram association is
platform-specific or an artefact of the ad.

**What TikTok cannot test.** No `saves` — the Display API does not return
`collect_count` (ADR 0001), so the strongest Instagram signal
(saves/view, +0.241 ad-controlled) has no counterpart here. No retention
of any kind, so `reels_skip_rate` is untestable. This is a partial
replication by construction, and the parts it cannot reach are named
rather than quietly dropped.

**One asymmetry to keep in mind when reading it.** TikTok view counts for
the owner's own account come from the official Display API and are exact,
so the denominators are sound. That is not true of competitor TikTok data
collected through the rounded fallback actor — but no competitor data
enters this analysis.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, "/home/rastlyn/virallab")

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from server.features.retention import (  # noqa: E402
    MIN_N, benjamini_hochberg, effect_label, fisher_ci, partial_spearman2,
    spearman, spearman_p,
)

#: The three measures the Instagram analysis settled on, strongest first.
FEATURES = ("edge_density", "background_edge_density", "text_region_score")

#: Instagram's ad-controlled figures, for side-by-side reading. Source:
#: docs/benchmarks/6c-visual-density.md §A.6.
IG_REFERENCE = {
    ("shares_per_view", "edge_density"): 0.162,
    ("shares_per_view", "text_region_score"): 0.229,
    ("likes_per_view", "edge_density"): 0.144,
    ("likes_per_view", "text_region_score"): 0.142,
}

OUTCOMES = ("shares_per_view", "likes_per_view", "comments_per_view")


def main() -> int:
    eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    S = sessionmaker(eng, expire_on_commit=False, future=True)
    out: dict = {"platform": "tiktok", "prediction": "positive"}

    with S() as s:
        rows = s.execute(text("""
            with latest as (
              select distinct on (s.video_id)
                     s.video_id, s.views, s.likes, s.comments, s.shares,
                     s.captured_at
              from metrics_snapshots s
                   join videos v on v.id = s.video_id
                   join creators c on c.id = v.creator_id
              where v.platform = 'tiktok' and c.is_own_account
              order by s.video_id, s.captured_at desc)
            select l.video_id, l.views, l.likes, l.comments, l.shares,
                   v.duration_s,
                   extract(epoch from (l.captured_at - v.posted_at))/86400.0
                     as age_days
            from latest l join videos v on v.id = l.video_id
            where l.views > 0 and v.posted_at is not null
        """)).mappings().all()

        base = {}
        for r in rows:
            views = float(r["views"])
            base[r["video_id"]] = {
                "shares_per_view": (float(r["shares"]) / views
                                    if r["shares"] is not None else None),
                "likes_per_view": (float(r["likes"]) / views
                                   if r["likes"] is not None else None),
                "comments_per_view": (float(r["comments"]) / views
                                      if r["comments"] is not None else None),
                "duration_s": (float(r["duration_s"])
                               if r["duration_s"] is not None else None),
                "age_days": float(r["age_days"]),
            }
        out["publications_with_metrics"] = len(base)

        feats: dict[str, dict[int, float]] = {}
        for name in FEATURES:
            got = s.execute(text("""
                select f.video_id, f.value_num
                from video_features f
                     join videos v on v.id = f.video_id
                     join creators c on c.id = v.creator_id
                where v.platform = 'tiktok' and c.is_own_account
                  and f.feature = :f and f.status = 'ok'
                  and f.method <> 'unavailable'
                  and f.value_num is not null
            """), {"f": name}).all()
            feats[name] = {vid: float(v) for vid, v in got}
        out["feature_coverage"] = {k: len(v) for k, v in feats.items()}

        results = []
        for outcome in OUTCOMES:
            for name in FEATURES:
                common = [vid for vid in feats[name]
                          if vid in base
                          and base[vid][outcome] is not None]
                xs = [feats[name][v] for v in common]
                ys = [base[v][outcome] for v in common]
                rec = {"outcome": outcome, "feature": name, "n": len(common)}
                if len(common) < MIN_N:
                    rec["note"] = "too few paired observations"
                    results.append(rec)
                    continue
                rho = spearman(xs, ys)
                rec["rho"] = round(rho, 3) if rho is not None else None
                rec["p"] = spearman_p(rec["rho"], len(common))
                ci = fisher_ci(rec["rho"], len(common))
                rec["ci"] = [round(c, 3) for c in ci] if ci else None
                rec["effect"] = effect_label(rec["rho"])
                withd = [v for v in common
                         if base[v]["duration_s"] is not None]
                if len(withd) >= MIN_N:
                    pr = partial_spearman2(
                        [feats[name][v] for v in withd],
                        [base[v][outcome] for v in withd],
                        [base[v]["duration_s"] for v in withd],
                        [base[v]["age_days"] for v in withd])
                    rec["partial_dur_age"] = (round(pr, 3) if pr is not None
                                              else None)
                    rec["partial_n"] = len(withd)
                ref = IG_REFERENCE.get((outcome, name))
                if ref is not None:
                    rec["instagram_ad_controlled"] = ref
                    rec["same_sign"] = (
                        None if rec["rho"] is None
                        else (rec["rho"] > 0) == (ref > 0))
                results.append(rec)

        qs = benjamini_hochberg([r.get("p") for r in results])
        for r, q in zip(results, qs):
            r["q"] = q
        out["associations"] = results

        tested = [r for r in results if r.get("instagram_ad_controlled")]
        agree = [r for r in tested if r.get("same_sign")]
        out["replication_summary"] = {
            "directional_predictions_tested": len(tested),
            "same_sign_as_instagram": len(agree),
            "surviving_fdr": [
                f"{r['outcome']}/{r['feature']}" for r in results
                if r.get("q") is not None and r["q"] < 0.05],
        }

    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
