"""Hook extraction, versioned storage, corrections, and the statistics.

Several of these pin down behaviour that was wrong first, and the comments
say which - a test whose reason is "this broke once" is worth more than one
that only restates the implementation.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from server.db.models import (Base, Creator, MetricsSnapshot, Video,
                              VideoFeature, VideoFeatureRun)
from server.features import extract as fx
from server.features import retention as rt
from shared import hookfeat, hookspeech
from tests.conftest import TEST_DB_URL, assert_disposable

DB_URL = TEST_DB_URL or ""
NOW = datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc)

pytestmark = pytest.mark.skipif(not DB_URL,
                                reason="VIRALLAB_TEST_DB_URL not set")

TABLES = ("video_features", "video_feature_runs", "metrics_snapshots",
          "video_master_links", "publication_links", "videos", "creators")


@pytest.fixture(scope="module")
def engine():
    assert_disposable(DB_URL)
    eng = create_engine(DB_URL, future=True)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    maker = sessionmaker(engine, expire_on_commit=False, future=True)
    with maker() as s:
        for t in TABLES:
            s.execute(text(f"truncate {t} restart identity cascade"))
        s.commit()
        yield s
        s.rollback()


@pytest.fixture
def owner(session):
    c = Creator(platform="instagram", handle="owner", external_id="ig-own",
                is_own_account=True, source="own_api")
    session.add(c)
    session.commit()
    return c


def add_video(session, creator, ext, *, duration=30.0, skip=None,
              posted_days_ago=100, media="available", watch_ms=None):
    v = Video(creator_id=creator.id, platform="instagram", external_id=ext,
              source="official_api", duration_s=duration,
              posted_at=NOW - timedelta(days=posted_days_ago),
              media_status=media,
              media_path=f"/tmp/ig_{ext}.mp4" if media == "available"
              else None)
    session.add(v)
    session.flush()
    if skip is not None:
        raw = {"reels_skip_rate": skip}
        if watch_ms is not None:
            raw["ig_reels_avg_watch_time"] = watch_ms
        session.add(MetricsSnapshot(video_id=v.id, captured_at=NOW,
                                    views=1000, raw=raw))
    session.commit()
    return v


def store(session, video, run, name, value, method="deterministic",
          version=hookfeat.METHOD_VERSION):
    fx._store(session, video, run,
              [{"name": name, "value": value, "method": method,
                "interval": (0.0, 3.0)}], method_version=version)
    session.commit()


@pytest.fixture
def run(session):
    r = VideoFeatureRun(method_version=hookfeat.METHOD_VERSION,
                        kind=fx.KIND_DETERMINISTIC, status="running")
    session.add(r)
    session.commit()
    return r


# ---------------------------------------------------------------------------
# versioned storage: the Part 3 requirement
# ---------------------------------------------------------------------------

def test_a_new_method_version_does_not_overwrite_the_old_one(session, owner,
                                                             run):
    v = add_video(session, owner, "V1")
    store(session, v, run, "cut_count", 3, version="hook-v1")
    store(session, v, run, "cut_count", 7, version="hook-v2")

    rows = session.scalars(select(VideoFeature).where(
        VideoFeature.feature == "cut_count")).all()
    assert len(rows) == 2
    assert {r.method_version: r.value_num for r in rows} == {
        "hook-v1": 3.0, "hook-v2": 7.0}


def test_rerunning_the_same_version_updates_in_place(session, owner, run):
    v = add_video(session, owner, "V1")
    store(session, v, run, "cut_count", 3)
    store(session, v, run, "cut_count", 4)
    rows = session.scalars(select(VideoFeature).where(
        VideoFeature.feature == "cut_count")).all()
    assert len(rows) == 1
    assert rows[0].value_num == 4.0


def test_a_value_changing_type_does_not_leave_both_columns_set(session,
                                                               owner, run):
    """A rerun that now yields a number where it yielded text must not
    leave the old text behind for `value` to pick up first."""
    v = add_video(session, owner, "V1")
    store(session, v, run, "thing", "some text")
    store(session, v, run, "thing", 12.5)
    row = session.scalars(select(VideoFeature).where(
        VideoFeature.feature == "thing")).one()
    assert row.value_text is None
    assert row.value_num == 12.5
    assert row.value == 12.5


def test_each_value_type_lands_in_its_own_column(session, owner, run):
    v = add_video(session, owner, "V1")
    store(session, v, run, "a_number", 4.5)
    store(session, v, run, "a_flag", True)
    store(session, v, run, "a_string", "hello")
    store(session, v, run, "a_list", [1.0, 2.0])
    rows = {r.feature: r for r in session.scalars(
        select(VideoFeature)).all()}
    assert rows["a_number"].value_num == 4.5
    assert rows["a_flag"].value_bool is True
    assert rows["a_flag"].value_num is None, "a bool must not become 1.0"
    assert rows["a_string"].value_text == "hello"
    assert rows["a_list"].value_json == {"items": [1.0, 2.0]}


# ---------------------------------------------------------------------------
# human corrections are additive
# ---------------------------------------------------------------------------

def test_a_correction_sits_beside_the_machine_value(session, owner, run):
    """Erasing the original would make it impossible to measure how often
    the machine was wrong, which is what corrections exist to reveal."""
    v = add_video(session, owner, "V1")
    store(session, v, run, "text_present_estimate", True, method="proxy")
    row = session.scalars(select(VideoFeature)).one()

    fx.correct(session, row.id, value=False, note="no text, it is a keyboard")
    session.commit()

    again = session.get(VideoFeature, row.id)
    assert again.value_bool is True            # the machine's answer kept
    assert again.corrected_value_bool is False  # the human's beside it
    assert again.effective_value is False       # analysis uses the human's
    assert again.is_corrected is True
    assert again.corrected_by == "human"
    assert "keyboard" in again.correction_note


def test_analysis_uses_the_corrected_value(session, owner, run):
    v = add_video(session, owner, "V1")
    store(session, v, run, "cut_count", 9)
    row = session.scalars(select(VideoFeature)).one()
    fx.correct(session, row.id, value=2)
    session.commit()
    matrix = fx.feature_matrix(session, ["cut_count"])
    assert matrix[v.id]["cut_count"] == 2.0


def test_correcting_an_unknown_row_is_refused(session):
    with pytest.raises(fx.ExtractionRefused):
        fx.correct(session, 999999, value=1)


# ---------------------------------------------------------------------------
# population selection
# ---------------------------------------------------------------------------

def test_only_own_account_videos_with_media_are_analysable(session, owner):
    add_video(session, owner, "MINE")
    add_video(session, owner, "NOMEDIA", media="absent")
    rival = Creator(platform="instagram", handle="rival",
                    external_id="ig-rival", is_own_account=False,
                    source="discovery")
    session.add(rival)
    session.commit()
    add_video(session, rival, "THEIRS")

    got = {v.external_id for v in fx.analysable(session)}
    assert got == {"MINE"}, (
        "extraction reads a local file; a competitor publication has none "
        "and must not acquire one as a side effect of analysis")


def test_the_retention_population_is_the_ceiling(session, owner):
    add_video(session, owner, "A", skip=50.0)
    add_video(session, owner, "B", skip=60.0)
    add_video(session, owner, "C")                       # media, no retention
    add_video(session, owner, "D", skip=70.0, media="absent")

    assert len(fx.analysable(session)) == 3
    assert len(fx.with_retention(session)) == 2


def test_the_sample_spans_the_retention_range(session, owner):
    for i in range(20):
        add_video(session, owner, f"V{i}", skip=30.0 + i * 3)
    sample = fx.representative_sample(session, n=5)
    assert len(sample) == 5
    skips = []
    for v in sample:
        snap = session.scalars(select(MetricsSnapshot).where(
            MetricsSnapshot.video_id == v.id)).one()
        skips.append(float(snap.raw["reels_skip_rate"]))
    # Both ends are present: a validation sample that only contains the
    # best cases cannot find what the extractor gets wrong.
    assert min(skips) == 30.0
    assert max(skips) == pytest.approx(30.0 + 19 * 3)


# ---------------------------------------------------------------------------
# the hallucination filter - every case here was produced by the real model
# on this real corpus
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "you", "You", "Thank you.", "Thank you for watching!", "Music",
    "컴퓨터 컴퓨터 컴퓨터 네, 네, 네",          # produced on an Italian pianist
    "la la la la la",
    "",
])
def test_known_music_hallucinations_are_rejected(text):
    bad, reason = hookspeech.looks_hallucinated(text)
    assert bad is True, f"{text!r} should have been rejected"
    assert reason


@pytest.mark.parametrize("text", [
    "There is a place you cannot go back to.",
    "guys thank you so much I just reached five thousand",
    "Hey, I was actually thinking that sometimes one piece of music is",
])
def test_real_transcripts_survive_the_filter(text):
    """All three were produced by the model and confirmed against the
    post's own caption, which the creator wrote."""
    bad, reason = hookspeech.looks_hallucinated(text)
    assert bad is False, f"{text!r} was wrongly rejected: {reason}"


def test_a_repeated_token_run_is_a_decoder_loop():
    bad, reason = hookspeech.looks_hallucinated("piano piano piano and more")
    assert bad is True
    assert "repeats" in reason


# ---------------------------------------------------------------------------
# statistics
# ---------------------------------------------------------------------------

def test_spearman_is_rank_based_not_linear():
    xs = [1, 2, 3, 4, 5]
    ys = [1, 4, 9, 16, 25]        # perfectly monotonic, not linear
    assert rt.spearman(xs, ys) == pytest.approx(1.0)


def test_spearman_handles_ties():
    assert rt.spearman([1, 1, 2, 2], [1, 1, 2, 2]) == pytest.approx(1.0)


def test_benjamini_hochberg_is_monotone_and_never_shrinks_a_p_value():
    ps = [0.001, 0.01, 0.02, 0.04, 0.3, 0.8]
    adj = rt.benjamini_hochberg(ps)
    assert all(a >= p for a, p in zip(adj, ps))
    assert adj == sorted(adj), "adjusted values must preserve the order"
    assert adj[-1] <= 1.0


def test_benjamini_hochberg_tolerates_missing_values():
    adj = rt.benjamini_hochberg([0.01, None, 0.5])
    assert adj[1] is None
    assert adj[0] is not None


def test_a_partial_correlation_removes_a_pure_confound():
    """x and y share a common cause z and are otherwise unrelated: the
    partial correlation should collapse."""
    z = [float(i) for i in range(30)]
    xs = [v + (0.1 if i % 2 else -0.1) for i, v in enumerate(z)]
    ys = [v + (0.1 if i % 3 else -0.1) for i, v in enumerate(z)]
    raw = rt.spearman(xs, ys)
    partial = rt.partial_spearman(xs, ys, z)
    assert raw > 0.9
    assert abs(partial) < abs(raw) / 2


def test_the_second_order_partial_controls_both_variables():
    z1 = [float(i) for i in range(40)]
    z2 = [float((i * 7) % 40) for i in range(40)]
    xs = [a + 0.5 * b for a, b in zip(z1, z2)]
    ys = [a + 0.5 * b for a, b in zip(z1, z2)]
    out = rt.partial_spearman2(xs, ys, z1, z2)
    assert out is not None


def test_a_confidence_interval_containing_zero_is_flagged():
    lo, hi = rt.fisher_ci(0.05, 40)
    assert lo < 0 < hi


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------

def test_too_small_a_sample_refuses_to_report_correlations(session, owner,
                                                           run):
    for i in range(5):
        v = add_video(session, owner, f"S{i}", skip=40.0 + i)
        store(session, v, run, "cut_count", i)
    rep = rt.analyse(session)
    assert rep.n_analysed == 5
    assert not rep.associations
    assert "below the minimum" in rep.verdict


def test_a_feature_with_no_variance_is_not_tested(session, owner, run):
    for i in range(25):
        v = add_video(session, owner, f"S{i}", skip=40.0 + i)
        store(session, v, run, "cut_count", 1)        # identical everywhere
    rep = rt.analyse(session, features=("cut_count",))
    a = rep.associations[0]
    assert a.rho is None
    assert "barely varies" in a.interpretation


def test_a_real_association_is_found_and_reported_with_uncertainty(
        session, owner, run):
    for i in range(30):
        v = add_video(session, owner, f"S{i}", skip=float(90 - i * 2))
        store(session, v, run, "text_region_score", float(i))
    rep = rt.analyse(session, features=("text_region_score",))
    a = rep.associations[0]
    assert a.rho is not None and a.rho < -0.9
    assert a.ci_low is not None and a.ci_high is not None
    assert a.p_adjusted is not None
    assert a.effect_size_label == "large"
    assert "less skipping" in a.direction


def test_the_report_always_states_its_limitations(session, owner, run):
    for i in range(25):
        v = add_video(session, owner, f"S{i}", skip=40.0 + i,
                      watch_ms=50000)
        store(session, v, run, "cut_count", float(i % 4))
    rep = rt.analyse(session)
    text = " ".join(rep.limitations)
    assert "One creator" in text
    assert "includes replays" in text
    assert "Correlational" in text
    assert "reels_skip_rate" in rep.outcome


def test_average_watch_time_is_never_used_as_a_completion_fraction():
    """It includes replays - one real video averages 3.38x its duration."""
    import inspect
    src = inspect.getsource(rt)
    assert "avg_watch_ms" in src
    # The outcome driving every correlation is the skip rate.
    assert rt.RetentionReport().outcome == "reels_skip_rate"


# ---------------------------------------------------------------------------
# the deterministic extractor's own contract
# ---------------------------------------------------------------------------

def test_the_extractor_declares_what_it_cannot_do():
    """A pass that silently omitted these would look complete."""
    names = {
        "on_screen_text_content", "performer_visible", "piano_visible",
        "hands_visible", "face_visible", "framing_class",
        "emotional_presentation",
    }
    import inspect
    src = inspect.getsource(hookfeat.extract_hook)
    for n in names:
        assert n in src, f"{n} is not declared unavailable anywhere"


def test_the_broken_speech_heuristic_is_gone():
    """It called speech on 12 of 12 when the truth was 0 of 12, and was
    deleted rather than tuned."""
    # The function is gone...
    assert not hasattr(hookfeat, "_speech_likelihood")
    # ...and no measurement by that name is emitted. The docstrings still
    # mention it, deliberately: the reason it was deleted is worth keeping.
    import inspect
    src = inspect.getsource(hookfeat.extract_hook)
    assert '"speech_likelihood"' not in src
    assert '_m("speech_present_estimate", None, "unavailable"' in src


def test_the_text_proxy_carries_its_measured_error_rate():
    assert 0.0 < hookfeat.TEXT_PROXY_PRECISION <= 1.0
    assert 0.0 < hookfeat.TEXT_PROXY_RECALL <= 1.0
    assert hookfeat.TEXT_PROXY_N >= 10
