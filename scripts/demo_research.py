"""End-to-end competitor research demonstration, on fixture data.

Runs the complete Phase 4 workflow against committed synthetic fixtures, so
it costs nothing, needs no credentials, and is reproducible. Everything it
prints is labelled: FIXTURE where the data is fabricated, and the
classification of every analytical claim.

    # against the disposable test database
    export VIRALLAB_TEST_DB_URL=postgresql+psycopg://...@127.0.0.1:5433/virallab_test
    python scripts/demo_research.py

    # or against any database you name
    python scripts/demo_research.py --db postgresql+psycopg://.../something_test

It refuses any database whose name does not end in `_test`, because it
truncates tables.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, func, select, text   # noqa: E402
from sqlalchemy.orm import sessionmaker                    # noqa: E402

from server import budget                                  # noqa: E402
from server.db.models import (Base, BreakthroughCandidate,  # noqa: E402
                              BudgetReservation, Creator, CreatorSnapshot,
                              DiscoveryJob, MetricsSnapshot, Video)
from server.discovery.base import (DiscoveryQuery, Platform,  # noqa: E402
                                   QueryKind)
from server.discovery.fixture import FixtureDiscoveryAdapter  # noqa: E402
from server.research import analysis_seam                   # noqa: E402
from server.research import baseline as bl                  # noqa: E402
from server.research import service                         # noqa: E402

TABLES = ("breakthrough_candidates", "metrics_snapshots",
          "video_master_links", "publication_links", "videos",
          "creator_snapshots", "creator_identity_members",
          "creator_identities", "local_masters", "performances",
          "creators", "discovery_jobs", "budget_reservations",
          "collection_runs")

DAY1 = datetime(2026, 10, 25, 9, 0, tzinfo=timezone.utc)
DAY8 = DAY1 + timedelta(days=7)


def rule(title: str) -> None:
    print(f"\n{'=' * 74}\n  {title}\n{'=' * 74}")


def head(title: str) -> None:
    print(f"\n-- {title}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("VIRALLAB_TEST_DB_URL"))
    ap.add_argument("--keep", action="store_true",
                    help="do not truncate first")
    args = ap.parse_args()

    if not args.db:
        print("  need --db or VIRALLAB_TEST_DB_URL")
        return 1
    name = args.db.rpartition("/")[2].split("?")[0]
    if not name.endswith("_test"):
        print(f"  refusing to run against {name!r}: this truncates tables, "
              f"so the target must obviously be disposable")
        return 2

    eng = create_engine(args.db, future=True)
    Base.metadata.create_all(eng)
    S = sessionmaker(eng, expire_on_commit=False, future=True)
    if not args.keep:
        with S() as s:
            for t in TABLES:
                s.execute(text(f"truncate {t} restart identity cascade"))
            s.commit()

    adapter = FixtureDiscoveryAdapter()

    rule("ViralLab Phase 4 - competitor research demonstration")
    print("  Provider      : FIXTURE (synthetic, fabricated, costs nothing)")
    print("  Database      : " + name)
    print("\n  Everything below is produced by the same code paths a live")
    print("  provider would use. Only the data is fabricated.")

    # ---------------------------------------------------------------- 0
    rule("0. Spending posture before anything runs")
    with S() as s:
        state = budget.budget_state(s)
        print(f"  monthly cap            : ${state['monthly_cap_usd']:.2f}")
        print(f"  providers authorised   : "
              f"{state['providers_authorised'] or 'none'}")
        print(f"  spending blocked       : {state['spending_blocked']}")
        print("\n  No paid provider is authorised, so research runs on")
        print("  fixtures. A live call would be refused, not attempted.")

    # ---------------------------------------------------------------- 1
    rule("1. What the provider can and cannot tell us")
    for plat in (Platform.TIKTOK, Platform.INSTAGRAM):
        caps = adapter.capabilities(plat)
        head(plat.value)
        print(f"     queries      : "
              f"{[q.value for q in caps.supported_queries]}")
        print(f"     followers    : {caps.follower_count}")
        print(f"     saves        : {caps.saves_count}")
        print(f"     media url    : {caps.media_url}")
        print(f"     full history : {caps.full_history}")
        missing = caps.missing_creator_fields()
        if missing:
            print(f"     NOT REPORTED : {list(missing)}")
    print("\n  Instagram reports no follower count at all. That is a verified")
    print("  property of the validated actors, so those fields stay null")
    print("  rather than being filled with zeros.")

    # ---------------------------------------------------------------- 2
    rule("2. Research a creator by handle")
    print('  Request: platform=tiktok  handle="https://www.tiktok.com/'
          '@demo_pianist"')
    with S() as s:
        out = service.research_creator(
            s, adapter, platform="tiktok",
            handle="https://www.tiktok.com/@demo_pianist", limit=200,
            now=DAY1)
        print(f"\n  resolved handle   : {out.handle}")
        print(f"  status            : {out.status}")
        print(f"  fixture data      : {out.is_fixture}")
        print(f"  publications      : {out.publications_seen} "
              f"({out.publications_new} new)")
        print(f"  observations saved: {out.snapshots_written}")
        print(f"  candidates found  : {out.breakthroughs_found}")
        print(f"  cost              : ${out.actual_usd or 0:.4f}")
        creator_id = out.creator_id

    # ---------------------------------------------------------------- 3
    rule("3. Repeat the same request immediately")
    with S() as s:
        again = service.research_creator(s, adapter, platform="tiktok",
                                         handle="@demo_pianist", now=DAY1)
        print(f"  reused existing research : {again.reused}")
        print(f"  {again.message}")

    # ---------------------------------------------------------------- 4
    rule("4. Creator profile")
    with S() as s:
        c = s.get(Creator, creator_id)
        obs = service.observations_for(s, creator_id)
        summary = bl.summarise(obs)
        print(f"  {c.platform} / {c.handle}   {c.display_name}")
        print(f"  source={c.source}  fixture={c.is_fixture}  "
              f"status={c.research_status}")
        head("figures")
        if c.follower_count is not None:
            print(f"     followers (latest)   : {c.follower_count:,}")
        else:
            print("     followers            : not reported by this source")
        print(f"     publications         : {summary.publications}")
        print(f"     median views         : "
              f"{summary.median_views:,.0f}")
        print(f"     median views, recent : "
              f"{summary.median_views_recent:,.0f}")
        print(f"     highest views        : {summary.top_views:,}")
        print(f"     published between    : "
              f"{summary.first_published:%Y-%m-%d} and "
              f"{summary.last_published:%Y-%m-%d}")
        print(f"     age span at observation: "
              f"{summary.age_span_days:.0f} days")
        head("caveats carried with those figures")
        for cav in summary.caveats:
            print(f"     * {cav}")

    # ---------------------------------------------------------------- 5
    rule("5. Publication explorer - ranked against the creator's own baseline")
    with S() as s:
        obs = service.observations_for(s, creator_id)
        points = bl.rolling_baseline(obs)
        scored = [p for p in points if p.relative_performance is not None]
        scored.sort(key=lambda p: -(p.relative_performance or 0))
        print(f"  {'posted':<12} {'views':>10} {'age':>6} "
              f"{'vs own baseline':>16} {'z':>6}")
        for p in scored[:8]:
            o = p.observation
            print(f"  {o.published_at:%Y-%m-%d} {o.views:>10,} "
                  f"{o.age_days_at_observation:>5.0f}d "
                  f"{p.relative_performance:>15.1f}x "
                  f"{p.robust_z or 0:>6.1f}")
        print("\n  Ranking by RAW views would put the oldest posts on top,")
        print("  because they have had the longest to accumulate. Ranking")
        print("  against the creator's own trailing window does not.")
        nobase = [p for p in points if p.relative_performance is None]
        print(f"\n  {len(nobase)} publication(s) have no relative figure: too")
        print("  few prior posts to form a baseline. Shown as unknown, not 1.0.")

    # ---------------------------------------------------------------- 6
    rule("6. Breakthrough analysis")
    with S() as s:
        rows = s.scalars(select(BreakthroughCandidate).where(
            BreakthroughCandidate.creator_id == creator_id).order_by(
            BreakthroughCandidate.publication_index)).all()
        videos = {v.id: v for v in s.scalars(select(Video).where(
            Video.creator_id == creator_id)).all()}
        bt = next((r for r in rows if r.classification
                   in ("inferred_breakthrough",
                       "verified_trajectory_change")), None)
        outliers = [r for r in rows
                    if r.classification == "observed_outlier"]

        head("observed outliers (facts about single publications)")
        for r in outliers:
            v = videos.get(r.video_id)
            print(f"     #{(r.publication_index or 0) + 1:<3} "
                  f"{v.posted_at:%Y-%m-%d}  {r.observed_views:>9,}  "
                  f"{r.relative_performance:>6.1f}x own baseline")

        if bt:
            v = videos.get(bt.video_id)
            head("change point")
            print(f"     classification : {bt.classification.upper()}")
            print(f"     publication    : #{(bt.publication_index or 0) + 1} "
                  f"of {len(videos)}, posted {v.posted_at:%Y-%m-%d}")
            print(f"     median before  : {bt.baseline_views:,.0f} views")
            print(f"     median after   : {bt.median_after:,.0f} views")
            print(f"     step           : "
                  f"{(bt.evidence or {}).get('step_ratio')}x")
            print(f"     sustained      : {bt.sustained}")
            print(f"     confidence     : {bt.confidence}")
            head("evidence this classification LACKS")
            for m in (bt.missing_evidence or {}).get("missing", []):
                print(f"     ! {m}")
            print("\n     So it is reported as an INFERRED breakthrough. The")
            print("     level changed here; nothing claims this video caused")
            print("     it, and no follower growth is asserted.")

    # ---------------------------------------------------------------- 7
    rule("7. The same creator on Instagram, and what differs")
    with S() as s:
        ig = service.research_creator(s, adapter, platform="instagram",
                                      handle="demo_pianist_ig", limit=200,
                                      now=DAY1)
        print(f"  publications : {ig.publications_seen}")
        print(f"  item errors  : {len(ig.item_errors)}")
        for e in ig.item_errors:
            print(f"     ! {e['error']}: {e['description']}")
        ig_creator = s.get(Creator, ig.creator_id)
        followers = (f"{ig_creator.follower_count:,}"
                     if ig_creator.follower_count is not None
                     else "not reported by this source")
        print(f"\n  followers    : {followers}")
        snaps = s.scalar(select(func.count(CreatorSnapshot.id)).where(
            CreatorSnapshot.creator_id == ig.creator_id))
        print(f"  account observations recorded : {snaps}")
        print("  -> none, because the source reports nothing account-level.")
        print("     An empty snapshot would imply we looked and saw zero.")
        print("\n  A private account in the fixture was reported by the")
        print("  provider as an error and kept. Dropping it would make a")
        print("  private account look like an empty one.")

    # ---------------------------------------------------------------- 8
    rule("8. Research again a week later - the temporal foundation")
    with S() as s:
        before = s.scalar(select(func.count(MetricsSnapshot.id)))
        service.research_creator(s, adapter, platform="tiktok",
                                 handle="demo_pianist", limit=200,
                                 refresh=True, now=DAY8)
        after = s.scalar(select(func.count(MetricsSnapshot.id)))
        vids = s.scalar(select(func.count(Video.id)))
        per_video = s.execute(
            select(func.count(MetricsSnapshot.id))
            .join(Video, Video.id == MetricsSnapshot.video_id)
            .where(Video.creator_id == creator_id)
            .group_by(MetricsSnapshot.video_id)).all()
        twice = sum(1 for (n,) in per_video if n >= 2)
        print(f"  snapshots  : {before} -> {after}  (appended)")
        print(f"  videos     : {vids}  (unchanged - nothing duplicated)")
        print(f"  publications now observed twice : {twice}")
        print("\n  This is what eventually upgrades an inferred breakthrough")
        print("  to a verified trajectory change. The detector already looks")
        print("  for exactly this evidence and refuses without it - nothing")
        print("  new has to be built, time has to pass.")

    # ---------------------------------------------------------------- 9
    rule("9. Handing a publication to the GPU worker (Phase 6 seam)")
    with S() as s:
        plan = analysis_seam.plan_for_creator(s, creator_id, tier="tier2")
        print(f"  tier              : {plan.tier}")
        print(f"  candidates        : {len(plan.candidates)}  "
              f"{plan.by_stratum}")
        print(f"  est. GPU time     : {plan.estimated_gpu_seconds:.0f}s")
        print(f"  est. transient media: "
              f"{plan.estimated_transient_mb:.0f} MB")
        head("notes")
        for n in plan.notes:
            print(f"     * {n}")
        if plan.refusals:
            head("refused")
            for r in plan.refusals:
                print(f"     ! {r}")
        try:
            analysis_seam.enqueue(s, plan)
        except analysis_seam.AnalysisRefused as exc:
            print(f"\n  enqueue() -> refused, correctly:\n     {exc}")

    # ---------------------------------------------------------------- 10
    rule("10. What was stored")
    with S() as s:
        for label, stmt in (
            ("creators", select(func.count(Creator.id))),
            ("  of which fixture",
             select(func.count(Creator.id)).where(
                 Creator.is_fixture.is_(True))),
            ("publications", select(func.count(Video.id))),
            ("performance observations",
             select(func.count(MetricsSnapshot.id))),
            ("account observations",
             select(func.count(CreatorSnapshot.id))),
            ("discovery jobs", select(func.count(DiscoveryJob.id))),
            ("breakthrough candidates",
             select(func.count(BreakthroughCandidate.id))),
            ("budget reservations",
             select(func.count(BudgetReservation.id))),
        ):
            print(f"  {label:<28} {s.scalar(stmt)}")
        spent = sum(j.actual_usd or 0.0 for j in
                    s.scalars(select(DiscoveryJob)).all())
        print(f"\n  total spent                  ${spent:.4f}")

    rule("Done")
    print("  Open the interface at /#/research to see this in the UI.")
    print("  Everything above is FIXTURE data and labelled as such in the")
    print("  database, the API and the interface.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
