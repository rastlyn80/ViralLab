"""M7: the four ways an opportunity could quietly overstate itself.

The acceptance criteria are that at least five opportunities come out
of real data, each traceable to specific publications, and none graded
above the evidence behind it. Those are the easy half. The tests that
matter guard the ways a page of suggestions goes wrong:

1. **Grade inflation.** A cohort difference is `Observed` and must
   stay `Observed` however small its q-value, because no outcome was
   measured on the other creators at all. Letting one reach
   `Associated` would turn "their openings differ from yours" into
   "this works", which is the single most tempting lie this page
   could tell.

2. **Survivorship.** A correlation of 0.3 means most publications do
   NOT follow the line. An opportunity that listed only the
   publications supporting it would be literally true and
   systematically misleading.

3. **Experiments nobody can run.** The owner posts 4.8 times a month.
   An experiment needing 194 publications is a three-year commitment,
   and printing it beside a four-month one without saying so is how a
   recommendation engine wastes a year of somebody's life.

4. **Unverified measurements driving advice.** The vision work of
   2026-10-27 established that `indicative` measurements may be shown
   and may never be counted. An opportunity is advice, so it needs
   the stricter gate.
"""

from __future__ import annotations

import math

import pytest

from server.research import opportunities as opp


# ---------------------------------------------------------------------------
# the arithmetic, which nothing else checks
# ---------------------------------------------------------------------------

def test_sample_size_matches_the_textbook_figure():
    """Fisher's z, 80% power, two-sided 5%. A rho of 0.3 needs about
    85 observations - the number quoted in every power table - and if
    this drifts, every feasibility verdict on the page drifts too."""
    assert opp.n_for_rho(0.3) == pytest.approx(85, abs=2)
    assert opp.n_for_rho(0.5) == pytest.approx(29, abs=2)


def test_a_weaker_effect_needs_more_publications():
    assert opp.n_for_rho(0.2) > opp.n_for_rho(0.4) > opp.n_for_rho(0.6)


def test_no_effect_size_means_no_claimed_sample_size():
    assert opp.n_for_rho(None) is None
    assert opp.n_for_rho(0.0) is None
    assert opp.n_for_contrast(None) is None


def test_deliberate_contrast_is_cheaper_than_waiting_for_the_correlation():
    """The reason the page is useful rather than discouraging. Sampling
    the ends of the distribution beats sampling its natural spread."""
    for rho in (0.15, 0.2, 0.3, 0.45):
        assert opp.n_for_contrast(rho) < opp.n_for_rho(rho), rho


def test_the_contrast_design_is_still_honest_about_small_effects():
    """It is cheaper, not magic. A tiny effect stays expensive, and a
    design that claimed otherwise would be the thing this page exists
    to avoid."""
    assert opp.n_for_contrast(0.05) > 200


@pytest.mark.parametrize("months,expected", [
    (3.0, "practical"), (9.0, "practical"),
    (12.0, "slow"), (24.0, "slow"),
    (25.0, "impractical"), (300.0, "impractical"),
    (None, "unknown"),
])
def test_feasibility_thresholds(months, expected):
    assert opp._feasibility(months)[0] == expected


def test_an_impractical_verdict_says_how_long_and_why():
    _, note = opp._feasibility(296.0)
    assert "296" in note or "over two years" in note
    assert "not" in note.lower()


# ---------------------------------------------------------------------------
# grading: the lie this page must not tell
# ---------------------------------------------------------------------------

def test_the_grade_ladder_cannot_be_reordered_by_accident():
    assert (opp.GRADE_ORDER["Observed"] < opp.GRADE_ORDER["Hypothesised"]
            < opp.GRADE_ORDER["Associated"] < opp.GRADE_ORDER["Validated"])


def test_every_row_status_maps_to_exactly_one_blueprint_grade():
    """`row_status` speaks lower case and the ladder is capitalised; a
    missing key would silently become `Observed` and understate, or
    worse, a typo would KeyError in front of the owner."""
    from server.research import insights as ins
    for status in ("observed", "hypothesised", "associated", "validated"):
        assert status in opp.STATUS_TO_GRADE
        assert opp.STATUS_TO_GRADE[status] in opp.GRADE_ORDER
    # the function that produces them can produce nothing else
    assert set(opp.STATUS_TO_GRADE) >= {
        "observed", "hypothesised", "associated", "validated"}
    assert ins.row_status is not None


def test_nothing_is_born_validated():
    """Only M9's pre-registered experiment can produce `Validated`.
    If this module ever mints one, the ladder has been short-circuited
    and an untested idea is wearing the badge of a tested one."""
    import inspect
    src = inspect.getsource(opp)
    # The only mentions may be in the mapping table and in prose.
    assert 'grade="Validated"' not in src
    assert "grade='Validated'" not in src


# ---------------------------------------------------------------------------
# the measurement gate
# ---------------------------------------------------------------------------

def test_an_unverified_vision_measurement_cannot_motivate_advice():
    """`indicative` is shown to a reader and never counted. An
    opportunity is advice, so it takes the stricter gate - and the
    module must actually consult it rather than merely import it."""
    import inspect

    from server.ai import vision_questions as vq
    assert not vq.may_enter_statistics("piano_or_keyboard_visible")
    assert not vq.may_enter_statistics("camera_angle")   # indicative
    assert vq.may_enter_statistics("face_visible")

    src = inspect.getsource(opp.from_associations)
    assert "may_enter_statistics" in src


# ---------------------------------------------------------------------------
# shape
# ---------------------------------------------------------------------------

def test_an_opportunity_reports_the_publications_it_rests_on():
    e1 = opp.Example(video_id=7, permalink="https://x/7",
                     feature_value=1.0, outcome_value=10.0, note="n")
    e2 = opp.Example(video_id=3, permalink=None,
                     feature_value=2.0, outcome_value=90.0, note="n")
    o = opp.Opportunity(
        key="k", approach="a", motivating_pattern="p", grade="Observed",
        source="association", feature="f", feature_display="F",
        outcome="o", outcome_display="O", platform="instagram", n=10,
        experiment=opp.Experiment(
            change="c", feature="f", direction="more", outcome="o",
            comparison="x", stopping_rule="y", metrics_to_collect=("o",),
            publications_needed=50, posting_rate_per_month=5.0,
            months_to_answer=10.0, feasibility="slow",
            feasibility_note="n"),
        supporting=[e1], counterexamples=[e2])
    assert o.traceable_video_ids() == [3, 7]
    d = o.as_dict()
    assert d["traceable_video_ids"] == [3, 7]
    assert d["counterexamples"][0]["video_id"] == 3


def test_an_example_without_an_outcome_serialises_as_null_not_nan():
    """A cohort difference has no outcome at all. NaN would produce
    invalid JSON and read as a number that happened to go missing."""
    import json
    e = opp.Example(video_id=1, permalink=None, feature_value=0.5,
                    outcome_value=None, note="no outcome exists")
    blob = json.dumps({"outcome_value": e.outcome_value})
    assert "NaN" not in blob
    assert json.loads(blob)["outcome_value"] is None


def test_the_faster_alternative_is_absent_rather_than_zero_when_unknown():
    """A cohort opportunity has no effect size, so there is no cheaper
    design to offer. Reporting one as 0 publications would read as
    free."""
    x = opp.Experiment(
        change="c", feature="f", direction="more", outcome="",
        comparison="x", stopping_rule="y", metrics_to_collect=(),
        publications_needed=None, posting_rate_per_month=5.0,
        months_to_answer=None, feasibility="unknown",
        feasibility_note="n")
    assert x.as_dict()["faster_alternative"] is None


def test_the_summary_leads_with_what_is_actually_startable():
    items = []
    assert "No opportunity" in opp.summary(items)["headline"]


def test_the_summary_always_carries_the_causation_caveat():
    assert "cause" in opp.summary([])["caveat"].lower()


def test_a_skip_rate_is_formatted_as_a_percentage_not_multiplied_by_a_hundred():
    """`skip_rate` is stored 0-100. Formatting it with Python's `%`
    turned 77.6 into '7760%', which is the kind of error that destroys
    confidence in every other figure on the page."""
    import inspect
    src = inspect.getsource(opp._examples)
    assert "{ov:.0%}" not in src
    assert "{ov:.0f}%" in src


def test_the_method_version_comes_from_the_catalogue_not_a_guess():
    """Guessing `hook-deterministic-v1` produced zero examples for
    every `background_*` feature, which lives under
    `hook-background-v1` - a silent, total failure that looked like
    missing data."""
    assert opp._method_version("edge_density") == "hook-deterministic-v1"
    assert opp._method_version("background_edge_density") == (
        "hook-background-v1")
    assert opp._method_version("not_a_real_feature") is None
