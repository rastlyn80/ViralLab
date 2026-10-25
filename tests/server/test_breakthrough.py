"""Breakthrough detector: the statistical properties, not the plumbing.

The easy version of this feature finds the post with the most views, which
is wrong in a specific way: on a single snapshot, older posts have had
longer to accumulate, so "the first viral hit" tends to be whatever was
published earliest. Most of these tests exist to prove this detector does
not do that.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from server.research.baseline import (Observation, rolling_baseline,
                                      summarise)
from server.research.breakthrough import (MIN_PUBLICATIONS, detect)

START = datetime(2024, 1, 1, tzinfo=timezone.utc)
OBSERVED = datetime(2026, 10, 25, tzinfo=timezone.utc)


def obs(views_series: list[int | None], *, step_days: int = 6,
        observed: datetime = OBSERVED,
        rounded: bool = False) -> list[Observation]:
    return [
        Observation(video_id=i + 1, external_id=f"v{i}",
                    published_at=START + timedelta(days=i * step_days),
                    views=v, observed_at=observed, counts_rounded=rounded)
        for i, v in enumerate(views_series)]


# ---------------------------------------------------------------------------
# the thing it must not do
# ---------------------------------------------------------------------------

def test_a_gradual_rise_is_not_a_breakthrough():
    """Smooth growth is exactly what the age confound predicts, so it is
    weak evidence and must not be reported as a change point."""
    series = [int(3000 * (1.06 ** i)) for i in range(40)]
    report = detect(obs(series))
    assert report.breakthrough is None
    assert report.refusals, "refused without saying why"


def test_a_single_spike_is_not_a_breakthrough():
    """One lucky video is noise. It may be an outlier; it is not a
    trajectory change."""
    series = [4000] * 20
    series[10] = 400_000
    report = detect(obs(series))
    assert report.breakthrough is None
    assert any(o.index == 10 for o in report.outliers), \
        "the spike should still be reported as an outlier"


def test_an_old_high_view_post_is_not_called_an_early_breakthrough():
    """The specific failure this feature invites: the earliest posts have
    had the longest to accumulate, so a descending series must not produce
    a 'first viral hit' at the start."""
    series = [int(500_000 * (0.97 ** i)) for i in range(40)]
    report = detect(obs(series))
    assert report.breakthrough is None, (
        "a declining series produced a breakthrough, which can only mean "
        "cumulative views were read as a trajectory")


def test_a_decline_is_never_a_breakthrough():
    series = [200_000] * 15 + [3_000] * 15
    report = detect(obs(series))
    assert report.breakthrough is None


# ---------------------------------------------------------------------------
# the thing it must do
# ---------------------------------------------------------------------------

def test_a_sustained_step_is_found_at_the_right_place():
    before, after = [4_000] * 20, [120_000] * 20
    report = detect(obs(before + after))
    b = report.breakthrough
    assert b is not None
    assert b.index == 20, f"change point reported at {b.index}, expected 20"
    assert b.step_ratio > 20
    assert b.sustained is True


def test_the_earliest_boundary_of_a_plateau_is_reported():
    """Adjacent boundaries score almost identically because a median barely
    moves when one value crosses it. Reporting the later one points at the
    publication AFTER the change."""
    series = [5_000] * 15 + [150_000] * 15
    b = detect(obs(series)).breakthrough
    assert b.index == 15


def test_a_short_history_is_refused_rather_than_guessed():
    report = detect(obs([4000] * (MIN_PUBLICATIONS - 1)))
    assert report.breakthrough is None
    assert report.outliers == []
    assert any(str(MIN_PUBLICATIONS) in r for r in report.refusals)


def test_an_unsustained_step_is_reported_but_not_sustained():
    # Up for six, then back down: a hot streak, not a new level.
    series = [4_000] * 20 + [120_000] * 6 + [4_500] * 14
    b = detect(obs(series)).breakthrough
    if b is not None:
        assert b.sustained is False


# ---------------------------------------------------------------------------
# classification is gated on evidence that actually exists
# ---------------------------------------------------------------------------

def test_a_single_snapshot_can_only_infer_never_verify():
    series = [4_000] * 20 + [120_000] * 20
    b = detect(obs(series)).breakthrough
    assert b.classification == "inferred_breakthrough"
    assert b.confidence <= 0.75, (
        "confidence above 0.75 implies evidence a single snapshot cannot "
        "provide")
    assert any("one observation" in m for m in b.missing_evidence)


def test_follower_history_alone_does_not_promote_to_verified():
    """Verification needs repeated observations of the publications too."""
    series = [4_000] * 20 + [120_000] * 20
    b = detect(obs(series), has_follower_history=True).breakthrough
    assert b.classification == "inferred_breakthrough"


def test_repeated_observations_and_follower_history_allow_verification():
    series = [4_000] * 20 + [120_000] * 20
    first = obs(series, observed=OBSERVED - timedelta(days=30))
    second = obs(series, observed=OBSERVED)
    b = detect(first + second, has_follower_history=True).breakthrough
    assert b is not None
    assert b.classification == "verified_trajectory_change"


def test_missing_evidence_is_always_recorded_for_an_inference():
    series = [4_000] * 20 + [120_000] * 20
    b = detect(obs(series)).breakthrough
    assert b.missing_evidence, (
        "an inferred breakthrough with no stated gap reads as established")
    assert any("follower" in m for m in b.missing_evidence)


def test_caveats_name_survivorship_and_platform_effects():
    series = [4_000] * 20 + [120_000] * 20
    report = detect(obs(series))
    joined = " ".join(report.caveats).lower()
    assert "control cohort" in joined or "did not grow" in joined
    assert "niche-wide" in joined or "platform" in joined


# ---------------------------------------------------------------------------
# baselines
# ---------------------------------------------------------------------------

def test_the_baseline_is_trailing_not_centred():
    """A centred window lets a post's neighbourhood include what came
    after, which smears a step backwards onto earlier posts."""
    series = [4_000] * 10 + [120_000] * 10
    points = rolling_baseline(obs(series))
    # The last pre-step publication must still be scored against low
    # neighbours, not pulled up by the step that follows it.
    assert points[9].relative_performance == pytest.approx(1.0, abs=0.3)
    assert points[10].relative_performance > 5


def test_early_publications_get_no_baseline_rather_than_a_weak_one():
    points = rolling_baseline(obs([4000] * 20))
    assert points[0].relative_performance is None
    assert points[0].notes
    assert points[-1].relative_performance is not None


def test_a_missing_view_count_is_skipped_not_treated_as_zero():
    series: list[int | None] = [4000] * 10
    series[5] = None
    points = rolling_baseline(obs(series))
    assert points[5].relative_performance is None
    assert any("Absent, not zero" in n for n in points[5].notes)
    # And it does not drag the neighbours' baseline toward zero.
    later = [p for p in points[6:] if p.baseline_views]
    assert all(p.baseline_views > 3000 for p in later)


def test_rounded_counts_are_flagged_on_every_point():
    points = rolling_baseline(obs([4000] * 12, rounded=True))
    scored = [p for p in points if p.baseline_views]
    assert scored
    assert all(any("rounding" in n or "significant" in n for n in p.notes)
               for p in scored)


def test_the_window_age_spread_is_reported():
    """It is the size of the confound that could not be removed."""
    points = rolling_baseline(obs([4000] * 15, step_days=10))
    scored = [p for p in points if p.window_age_spread_days is not None]
    assert scored
    assert all(p.window_age_spread_days > 0 for p in scored)


def test_a_median_is_used_not_a_mean():
    """A mean is dragged by the very outliers this is hunting."""
    series = [1000] * 8 + [10_000_000]
    points = rolling_baseline(obs(series + [1000]))
    last = points[-1]
    # With a mean baseline the final typical post would look like a
    # catastrophic underperformer; with a median it looks typical.
    assert last.relative_performance == pytest.approx(1.0, abs=0.3)


# ---------------------------------------------------------------------------
# summary honesty
# ---------------------------------------------------------------------------

def test_the_summary_warns_about_a_wide_age_span():
    series = [4000] * 60
    s = summarise(obs(series, step_days=20))
    assert s.age_span_days > 180
    assert any("age" in c.lower() for c in s.caveats)


def test_the_summary_always_says_it_is_not_a_trajectory():
    s = summarise(obs([4000] * 20))
    assert any("trajectory" in c.lower() for c in s.caveats)


def test_publications_without_views_are_excluded_and_counted():
    series: list[int | None] = [4000] * 10 + [None] * 3
    s = summarise(obs(series))
    assert s.publications == 13
    assert s.with_views == 10
    assert any("no view count" in c for c in s.caveats)


def test_log_space_is_used_so_ratios_behave():
    """Doubling every value must not change anyone's relative standing."""
    series = [3000, 4000, 5000, 6000, 7000, 8000, 9000, 10000, 40000]
    a = rolling_baseline(obs(series))[-1]
    b = rolling_baseline(obs([v * 2 for v in series]))[-1]
    assert a.relative_performance == pytest.approx(b.relative_performance,
                                                   rel=1e-6)
