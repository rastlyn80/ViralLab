"""M1: the ad-exposure covariate and the manual analytics import.

The properties guarded here are the ones whose failure would be invisible.
A covariate that silently mixes two incompatible sources, or an importer
that quietly maps the wrong column to "average watch time", produces
numbers that look exactly as trustworthy as correct ones. These tests exist
because nothing downstream could tell the difference.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from server.analytics import ad_exposure, imports
from server.analytics.imports import ImportRefused
from server.db.models import (AnalyticsImport, Base, Creator, ImportedMetric,
                              MetricsSnapshot, Video)
from server.features.retention import (partial_spearman, partial_spearman2,
                                       partial_spearman_n, spearman)
from tests.conftest import TEST_DB_URL, assert_disposable


# ---------------------------------------------------------------------------
# pure functions — no database needed
# ---------------------------------------------------------------------------

def test_partial_spearman_n_reproduces_the_existing_implementations():
    """The generic recursion must not change published Phase 6C numbers.

    `partial_spearman` and `partial_spearman2` produced the figures in
    docs/benchmarks/6c-visual-density.md. If the generalised version
    disagrees with them, every published partial correlation is in
    question, so this is checked rather than assumed.
    """
    import random
    random.seed(20261026)
    n = 80
    x = [random.random() for _ in range(n)]
    y = [random.random() for _ in range(n)]
    z = [random.random() for _ in range(n)]
    w = [random.random() for _ in range(n)]

    assert partial_spearman_n(x, y, []) == pytest.approx(spearman(x, y))
    assert partial_spearman_n(x, y, [z]) == pytest.approx(
        partial_spearman(x, y, z))
    assert partial_spearman_n(x, y, [z, w]) == pytest.approx(
        partial_spearman2(x, y, z, w))


def test_surface_residual_uses_the_largest_defensible_basis():
    """`crossposted_views` is 0 for a post never shared to Facebook.

    Measured on the real account: 15 of 15 such publications reported
    `crossposted_views == 0` alongside `facebook_views == 0`. Taking that 0
    as the organic-plus-Facebook basis would make every one of them read as
    100% paid, which is the single most damaging way this could fail.
    """
    e = ad_exposure._from_surface(1, {
        "views": 3645, "facebook_views": 0, "crossposted_views": 0,
        "total_views": 3645})
    assert e.available
    assert e.value == pytest.approx(0.0)
    assert e.detail["basis"] == "views+facebook_views"


def test_surface_residual_refuses_when_facebook_views_is_unknown():
    """A gap that a missing `facebook_views` could explain is not paid.

    Twelve publications on the real account return "Meta error -1: Fatal"
    for `facebook_views` on every retry. For those the Facebook surface is
    unobtainable, so the difference between `total_views` and `views`
    cannot be attributed — and two of the twelve carried the largest gaps
    in the corpus, so guessing would have been expensive.
    """
    e = ad_exposure._from_surface(1, {
        "views": 27994, "facebook_views": None, "crossposted_views": None,
        "total_views": 1733698})
    assert not e.available
    assert "facebook_views unknown" in e.unavailable_reason
    # And the *same* shape with no gap is answerable, because there is
    # nothing left to attribute.
    ok = ad_exposure._from_surface(1, {
        "views": 3645, "facebook_views": None, "crossposted_views": None,
        "total_views": 3645})
    assert ok.available and ok.value == pytest.approx(0.0)


def test_crossposted_views_rescues_a_failed_facebook_views_call():
    """A Facebook-inclusive basis is a Facebook-inclusive basis.

    `crossposted_views == views + facebook_views`, so a publication whose
    `facebook_views` call failed but whose `crossposted_views` succeeded
    has Facebook accounted for and its residual IS attributable. Refusing
    on `facebook_views is None` alone would discard it.
    """
    e = ad_exposure._from_surface(1, {
        "views": 1000, "facebook_views": None, "crossposted_views": 1500,
        "total_views": 6000})
    assert e.available
    assert e.detail["basis"] == "crossposted_views"
    assert e.value == pytest.approx(0.75)


def test_surface_residual_reports_a_broken_identity_rather_than_clamping():
    e = ad_exposure._from_surface(1, {
        "views": 500, "facebook_views": 100, "crossposted_views": 600,
        "total_views": 400})
    assert not e.available
    assert "negative residual" in e.unavailable_reason


def test_homogeneous_never_mixes_sources():
    """Partialling out a column that means two different things is not a
    control. It is a new variable with no definition."""
    exposures = {
        1: ad_exposure.Exposure(1, 0.9, "surface_residual"),
        2: ad_exposure.Exposure(2, 0.8, "surface_residual"),
        3: ad_exposure.Exposure(3, 12.0, "gap_proxy"),
        4: ad_exposure.Exposure(4, None, "none", "nothing to go on"),
    }
    src, vals = ad_exposure.homogeneous(exposures, [1, 2, 3, 4])
    assert src == "surface_residual"
    assert set(vals) == {1, 2}
    assert 3 not in vals, "a gap_proxy value leaked into a residual column"


def test_coverage_counts_unknowns_separately():
    exposures = {
        1: ad_exposure.Exposure(1, 0.5, "surface_residual"),
        2: ad_exposure.Exposure(2, None, "surface_residual", "unobtainable"),
    }
    cov = ad_exposure.coverage(exposures)
    assert cov == {"total": 2, "by_source": {"surface_residual": 1},
                   "unknown": 1, "method_version": ad_exposure.METHOD_VERSION}


def test_surface_metrics_fall_back_to_one_call_per_metric():
    """One unsupported metric must not cost us the other two.

    Meta fails a combined insights call entirely when any single metric
    name is unsupported for the media product type, and `crossposted_views`
    is unsupported for everything that is not a reel. The first version of
    this collector requested all three together and recorded `total_views`
    for 68 of 210 publications as a result — while the probe, which
    requested them singly, got it for all 385.
    """
    from server.collectors.instagram_official import (
        InstagramOfficialAdapter, SURFACE_METRICS,
    )

    calls: list[str] = []

    class Stub(InstagramOfficialAdapter):
        def __init__(self):            # no token store needed
            self.buc = None

        def _get(self, account_ref, path, **params):
            metric = params.get("metric", "")
            calls.append(metric)
            if path.endswith("/insights") and "," in metric:
                if "crossposted_views" in metric and "total_views" in metric:
                    raise RuntimeError(
                        "Meta error 100: does not support the "
                        "crossposted_views metric for this media product "
                        "type")
                # The core and retention sets succeed.
                return {"data": [{"name": n, "values": [{"value": 1}]}
                                 for n in metric.split(",")]}
            if metric == "crossposted_views":
                raise RuntimeError("Meta error 100: unsupported")
            return {"data": [{"name": metric,
                              "values": [{"value": 4242}]}]}

    snaps = list(Stub().fetch_metrics("acct", ["media1"]))
    assert len(snaps) == 1
    snap = snaps[0]
    # The sibling's failure did not take total_views with it.
    assert snap.total_views == 4242
    assert snap.facebook_views == 4242
    assert snap.crossposted_views is None
    assert snap.raw["surface_status"] == "partial"
    assert "crossposted_views" in snap.raw["surface_unavailable"]
    # And it did fall back: each metric was asked for on its own.
    assert set(SURFACE_METRICS) <= set(calls)


def test_spectral_centroid_is_finite_on_silence():
    """A silent clip must not produce NaN.

    `spectral_centroid_hz` divided by `spec.sum()` where its neighbour
    divided by `spec.sum() + eps`. On silence that is 0/0. NaN is the worst
    possible result here because it passes every `isinstance(x, float)`
    check downstream and then propagates through any statistic it enters
    without raising. Found while extracting features for the 210
    publications M1 showed were missing them.
    """
    import numpy as np

    from shared import hookfeat

    n = 16
    frames = np.zeros((n, hookfeat.AUDIO_FRAME), dtype=np.float32)
    spec = np.zeros((n, hookfeat.AUDIO_FRAME // 2 + 1), dtype=np.float32)
    out = hookfeat._spectral_stats(frames, spec)
    assert out, "silence should still yield the spectral block"
    for name, value in out.items():
        assert value == value, f"{name} is NaN on a silent clip"
        assert value not in (float("inf"), float("-inf")), f"{name} is inf"


# ---------------------------------------------------------------------------
# the importer
# ---------------------------------------------------------------------------

TIKTOK_CSV = (
    "Video title,Post time,Video link,Total views,Total likes,"
    "Total comments,Total shares,Average watch time,Watched full video,"
    "Some column we do not know\n"
    # The thousands separator is quoted, as a real export writes it.
    "Chopin Ballade,2026-09-01 10:00,"
    "https://www.tiktok.com/@gb/video/7123456789012345678,"
    "\"12,400\",830,41,22,8.4s,43.2%,whatever\n"
    "Liszt Study,2026-09-04 18:30,"
    "https://www.tiktok.com/@gb/video/7123456789012345679,"
    "2.1K,96,3,1,1m 12s,12%,whatever\n"
)


def test_tiktok_export_parses_units_and_reports_unmapped_columns():
    p = imports.parse(TIKTOK_CSV, source="tiktok_studio", platform="tiktok")
    assert len(p.rows) == 2
    first = p.rows[0]
    assert first.metrics["views"] == (12400.0, "count")
    assert first.metrics["avg_watch_time_s"] == (8.4, "s")
    # A percentage lands as a fraction, under a name that says so.
    assert first.metrics["watched_full_video_rate"] == (
        pytest.approx(0.432), "fraction")
    # "2.1K" and "1m 12s" are both forms these dashboards emit.
    assert p.rows[1].metrics["views"] == (2100.0, "count")
    assert p.rows[1].metrics["avg_watch_time_s"] == (72.0, "s")
    assert "Some column we do not know" in p.unmapped_columns
    assert p.warnings and "not recognised" in p.warnings[0]


def test_percent_columns_are_not_divided_twice():
    """`watched_full_video` may arrive as "43.2%", 43.2 or 0.432.

    Dividing the third form by 100 would turn a 43% completion rate into
    0.4% — a silent 100x error in one of the two metrics the whole TikTok
    export exists to obtain. The rule: divide when a `%` is present or the
    value exceeds 1; otherwise keep it and say so.
    """
    assert imports._to_float("43.2%", is_percent=True)[0] == pytest.approx(
        0.432)
    assert imports._to_float("43.2", is_percent=True)[0] == pytest.approx(
        0.432)
    val, note = imports._to_float("0.432", is_percent=True)
    assert val == pytest.approx(0.432), "an existing fraction was rescaled"
    assert "100x" in note, "the ambiguous case was not flagged"
    # Non-percent columns are never touched by any of this.
    assert imports._to_float("12400", is_percent=False)[0] == 12400.0


def test_an_ambiguous_percent_reaches_the_import_warnings():
    csv = ("Video link,Watched full video\n"
           "https://www.tiktok.com/@gb/video/7123456789012345678,0.43\n")
    p = imports.parse(csv, source="tiktok_studio", platform="tiktok")
    assert p.rows[0].metrics["watched_full_video_rate"][0] == pytest.approx(
        0.43)
    assert any("100x" in w for w in p.warnings), p.warnings


def test_importer_refuses_a_file_it_cannot_attach_to_publications():
    """Without an identifier column the rows describe nothing. Importing
    them anyway would create metrics attached to no publication."""
    with pytest.raises(ImportRefused, match="no column identifies"):
        imports.parse("Total views,Total likes\n100,5\n",
                      source="tiktok_studio", platform="tiktok")


def test_importer_refuses_a_file_with_no_recognised_metric():
    with pytest.raises(ImportRefused, match="no recognised metric"):
        imports.parse("Video link,Mystery\nhttps://x/video/1,5\n",
                      source="tiktok_studio", platform="tiktok")


def test_heading_match_is_exact_not_substring():
    """"Total watch time" must not become "Average watch time".

    Substring matching would map both to the same metric and silently turn
    a sum into a per-viewer retention figure.
    """
    p = imports.parse(
        "Video link,Total views,Total watch time\n"
        "https://www.tiktok.com/@gb/video/7123456789012345678,10,999\n",
        source="tiktok_studio", platform="tiktok")
    assert "avg_watch_time_s" not in p.rows[0].metrics
    assert "Total watch time" in p.unmapped_columns


def test_external_id_extraction():
    assert imports.extract_external_id(
        "https://www.tiktok.com/@gb/video/7123456789012345678"
    ) == "7123456789012345678"
    assert imports.extract_external_id("7123456789012345678") == \
        "7123456789012345678"
    assert imports.extract_external_id("not a link at all") is None


# ---------------------------------------------------------------------------
# database-backed
# ---------------------------------------------------------------------------

DB_URL = TEST_DB_URL
dbonly = pytest.mark.skipif(not DB_URL,
                            reason="VIRALLAB_TEST_DB_URL not set")
if DB_URL:
    assert_disposable(DB_URL)


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(DB_URL, future=True)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    S = sessionmaker(engine, expire_on_commit=False, future=True)
    with S() as s:
        s.execute(text("truncate imported_metrics, analytics_imports, "
                       "ad_exposures, metrics_snapshots, video_features, "
                       "video_feature_runs, videos, creators "
                       "restart identity cascade"))
        s.commit()
        yield s
        s.rollback()


def _own_creator(s) -> Creator:
    c = Creator(platform="instagram", handle="owner", is_own_account=True,
                source="own_api")
    s.add(c)
    s.flush()
    return c


@dbonly
def test_resolve_prefers_measured_surface_over_the_gap_proxy(session):
    """The gap proxy is a fallback, not a peer.

    BLUEPRINT §1.2 used days-to-next-post because nothing better existed.
    Something better now exists for any publication with surface metrics,
    and the resolver must take it — the gap also buys organic accumulation,
    so it conflates the confound with the thing being measured.
    """
    c = _own_creator(session)
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    v1 = Video(creator_id=c.id, platform="instagram", external_id="a",
               source="official_api", posted_at=base)
    v2 = Video(creator_id=c.id, platform="instagram", external_id="b",
               source="official_api", posted_at=base + timedelta(days=5))
    session.add_all([v1, v2])
    session.flush()
    # v1 has surface metrics; v2 has none and no following post.
    session.add(MetricsSnapshot(
        video_id=v1.id, captured_at=base + timedelta(days=30),
        views=1000, total_views=5000, facebook_views=0,
        crossposted_views=1000))
    session.commit()

    out = ad_exposure.resolve(session, platform="instagram")
    assert out[v1.id].source == "surface_residual"
    assert out[v1.id].value == pytest.approx(0.8)
    # v2 has no next post, so no gap either: it must be an explicit
    # unknown rather than a missing key.
    assert v2.id in out and not out[v2.id].available


@dbonly
def test_every_publication_gets_a_value_or_an_explicit_unknown(session):
    """M1's acceptance criterion, as an executable check."""
    c = _own_creator(session)
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    for i in range(5):
        session.add(Video(creator_id=c.id, platform="instagram",
                          external_id=f"v{i}", source="official_api",
                          posted_at=base + timedelta(days=i * 3)))
    session.commit()
    out = ad_exposure.resolve(session, platform="instagram")
    ids = {v.id for v in session.scalars(select(Video)).all()}
    assert set(out) == ids
    for e in out.values():
        assert e.available or e.unavailable_reason, (
            "a publication with neither a value nor a stated reason")


@dbonly
def test_ad_window_reports_months_and_carries_its_caveat(session):
    c = _own_creator(session)
    for i in range(6):
        v = Video(creator_id=c.id, platform="instagram",
                  external_id=f"w{i}", source="official_api",
                  posted_at=datetime(2026, 7, 1, tzinfo=timezone.utc)
                  + timedelta(days=i))
        session.add(v)
        session.flush()
        session.add(MetricsSnapshot(
            video_id=v.id, captured_at=datetime(2026, 9, 1,
                                                tzinfo=timezone.utc),
            views=100, total_views=1000, facebook_views=0,
            crossposted_views=100))
    session.commit()
    w = ad_exposure.ad_window(session)
    assert w["first_month_above_threshold"] == "2026-07"
    assert "UPPER BOUND" in w["caveat"]


@dbonly
def test_store_refuses_to_persist_a_non_finite_measurement(session):
    """NaN must never reach `video_features`.

    Two traps here, both hit for real on 2026-10-26.

    1. `spectral_centroid_hz` divided by a spectrum sum that is exactly 0
       on a silent clip, producing NaN, which `_store` then wrote as an
       ordinary float.
    2. **Postgres treats `NaN = NaN` as TRUE**, so the obvious audit query
       `where value_num != value_num` matches nothing and reports a clean
       table. The correct predicate is `value_num = 'NaN'::float8`, and
       this test uses it — the first audit of this used the wrong one and
       said there were zero NaNs while one was sitting in the table.
    """
    from server.db.models import VideoFeature, VideoFeatureRun
    from server.features.extract import _store

    c = _own_creator(session)
    v = Video(creator_id=c.id, platform="instagram", external_id="nanvid",
              source="official_api")
    session.add(v)
    session.flush()
    run = VideoFeatureRun(method_version="t-v1", kind="test",
                          status="running", videos_requested=1)
    session.add(run)
    session.flush()

    _store(session, v, run, [
        {"name": "finite_one", "value": 1.5, "method": "deterministic"},
        {"name": "nan_one", "value": float("nan"), "method": "deterministic"},
        {"name": "inf_one", "value": float("inf"), "method": "deterministic"},
    ], method_version="t-v1")
    session.flush()

    rows = {r.feature: r for r in session.scalars(select(VideoFeature).where(
        VideoFeature.video_id == v.id)).all()}
    assert rows["finite_one"].value_num == pytest.approx(1.5)
    for bad in ("nan_one", "inf_one"):
        assert rows[bad].value_num is None, f"{bad} was persisted"
        assert rows[bad].method == "unavailable"
        assert "non-finite" in (rows[bad].unavailable_reason or "")

    # And the audit predicate itself, so a future reader copies the right
    # one rather than the intuitive wrong one.
    n_nan = session.execute(text(
        "select count(*) from video_features "
        "where value_num = 'NaN'::float8")).scalar()
    assert n_nan == 0


@dbonly
def test_import_is_idempotent_on_the_file_bytes(session, tmp_path):
    """Re-submitting the same export must not double the metrics."""
    c = Creator(platform="tiktok", handle="owner", is_own_account=True,
                source="own_api")
    session.add(c)
    session.flush()
    session.add(Video(creator_id=c.id, platform="tiktok",
                      external_id="7123456789012345678",
                      source="official_api",
                      posted_at=datetime(2026, 9, 1, tzinfo=timezone.utc)))
    session.commit()

    f = tmp_path / "tiktok.csv"
    f.write_text(TIKTOK_CSV, encoding="utf-8")

    imp1, s1 = imports.ingest(session, path=f, source="tiktok_studio",
                              platform="tiktok", exported_at=date(2026, 10, 1))
    session.commit()
    assert s1["matched"] == 1
    assert s1["unmatched"] == 1
    n_first = session.scalar(
        select(text("count(*)")).select_from(ImportedMetric))

    imp2, s2 = imports.ingest(session, path=f, source="tiktok_studio",
                              platform="tiktok", exported_at=date(2026, 10, 1))
    session.commit()
    assert imp2.id == imp1.id
    assert s2.get("already_imported") is True
    n_second = session.scalar(
        select(text("count(*)")).select_from(ImportedMetric))
    assert n_second == n_first


@dbonly
def test_import_age_anchors_against_the_known_publication_time(session,
                                                               tmp_path):
    """An imported figure carries the age at which it was observed.

    Without it the number cannot be compared to anything (CLAUDE.md §3.8a),
    and the export's own date column is frequently date-only.
    """
    c = Creator(platform="tiktok", handle="owner", is_own_account=True,
                source="own_api")
    session.add(c)
    session.flush()
    session.add(Video(creator_id=c.id, platform="tiktok",
                      external_id="7123456789012345678",
                      source="official_api",
                      posted_at=datetime(2026, 9, 1, tzinfo=timezone.utc)))
    session.commit()

    f = tmp_path / "tt.csv"
    f.write_text(TIKTOK_CSV, encoding="utf-8")
    imports.ingest(session, path=f, source="tiktok_studio",
                   platform="tiktok", exported_at=date(2026, 10, 1))
    session.commit()

    row = session.scalar(select(ImportedMetric).where(
        ImportedMetric.metric == "avg_watch_time_s",
        ImportedMetric.video_id.isnot(None)))
    assert row is not None
    assert row.unit == "s"
    assert row.provenance == "manual_export"
    assert row.observed_age_days == pytest.approx(30.0, abs=0.01)


@dbonly
def test_unmatched_rows_are_kept_not_dropped(session, tmp_path):
    """An export row for a publication we never collected is evidence about
    coverage. Dropping it would hide the gap."""
    f = tmp_path / "tt.csv"
    f.write_text(TIKTOK_CSV, encoding="utf-8")
    imports.ingest(session, path=f, source="tiktok_studio",
                   platform="tiktok", exported_at=date(2026, 10, 1))
    session.commit()
    rows = session.scalars(select(ImportedMetric)).all()
    assert rows, "nothing was imported at all"
    assert all(r.video_id is None for r in rows)
    imp = session.scalar(select(AnalyticsImport))
    assert imp.row_count == 2 and imp.matched_count == 0
    assert imp.parse_notes["unmapped_columns"]
