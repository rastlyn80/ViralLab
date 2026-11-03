"""When he posted, and the three things that answer cannot be.

The owner asked the chat, on a Monday afternoon, what time to post
in the UK. It said it had no access to posting times. `posted_at`
is populated on all 9,142 publications.

The data was never the problem, so these tests are not mainly about
the arithmetic. They are about the claims the module is allowed to
make with it: that it is his local hour and not his audience's,
that it cannot be replicated on other creators, and that the best
hour on the list is not a recommendation.
"""

from __future__ import annotations

import datetime as dt

import pytest

from server.research import timing as tm


# ---------------------------------------------------------------------------
# the banding
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hour,part", [
    (0, "night"), (5, "night"), (23, "night"),
    (6, "morning"), (11, "morning"),
    (12, "afternoon"), (17, "afternoon"),
    (18, "evening"), (22, "evening"),
])
def test_every_hour_lands_in_the_part_of_day_it_should(hour, part):
    assert tm.part_of_day(hour) == part


def test_the_bands_cover_the_clock_exactly_once():
    """A gap would silently drop publications; an overlap would
    count them twice and make the Mann-Whitney compare a group
    against itself."""
    seen = [tm.part_of_day(h) for h in range(24)]
    assert len(seen) == 24
    assert set(seen) == {p[0] for p in tm.PARTS}


def test_four_bands_not_twenty_four():
    """Twenty-four tests on a few hundred publications finds an
    hour whether or not one is there. The hours are still shown -
    `by_hour` - and marked descriptive."""
    assert len(tm.PARTS) == 4


# ---------------------------------------------------------------------------
# what it refuses to claim
# ---------------------------------------------------------------------------

def _built(parts, days, n=200):
    """`build`'s output without a database, by feeding `_compare`
    the grouped z values directly."""
    return tm._compare(parts, tuple(parts)), tm._compare(days, tuple(days))


def test_a_small_group_is_described_and_not_tested():
    groups = {"afternoon": [0.5] * 40, "night": [0.1] * 4}
    out = {e["when"]: e for e in tm._compare(groups,
                                             ("afternoon", "night"))}
    assert out["night"]["tested"] is False
    assert out["night"]["p_value"] is None
    assert out["night"]["separates"] is False
    assert str(tm.MIN_PER_GROUP) in out["night"]["why_not_tested"]
    # Still counted, so the reader can see it exists.
    assert out["night"]["publications"] == 4


def test_a_group_with_no_difference_does_not_separate():
    groups = {"a": [0.0, 0.1, -0.1] * 10, "b": [0.0, 0.1, -0.1] * 10}
    out = tm._compare(groups, ("a", "b"))
    assert all(not e["separates"] for e in out)


def test_the_headline_says_so_when_nothing_separates():
    head = tm._headline([], 250)
    assert "no part of the day and no weekday separates" in head
    assert "250" in head


def test_the_headline_on_an_empty_corpus_does_not_pretend():
    assert "No publication" in tm._headline([], 0)


# ---------------------------------------------------------------------------
# the three caveats, which are the point
# ---------------------------------------------------------------------------

def test_it_says_the_hours_are_his_and_not_his_audiences():
    """A good hour here is a fact about his publications. Reading
    it as "my viewers are awake then" needs the audience's
    timezone, which only each platform's own analytics has."""
    doc = tm.__doc__ or ""
    assert "audience" in doc.lower()
    assert str(tm.OWNER_ZONE) == "Europe/London"


def test_it_refuses_the_replication_bar_rather_than_faking_it():
    """ADR 0011 made replication across independent creators the
    promotion bar. This is the one measurement that cannot reach
    it, and the reason is not sample size: a competitor's local
    hour is unknown, so pooling creators measures the cohort's
    timezone spread. Saying that is better than quietly reporting
    a cross-creator number as replication.
    """
    import inspect

    src = inspect.getsource(tm.build)
    assert "can_this_be_replicated" in src
    assert "but not where " in src
    doc = tm.__doc__ or ""
    assert "cannot be replicated on other creators" in doc


def test_the_confound_travels_with_the_answer():
    import inspect

    src = inspect.getsource(tm.build)
    assert "the_confound" in src
    assert "concert day" in src
    assert '"grade": "Observed"' in src


def test_advice_is_not_called_advice_in_the_answer():
    """`advice()` is the chat's entry point and the one most likely
    to be read as an instruction, so the refusal is in the payload
    rather than left to the prompt."""
    import inspect

    src = inspect.getsource(tm.advice)
    assert "why_this_is_not_a_recommendation" in src
    assert "ranking of noise" in src


# ---------------------------------------------------------------------------
# the chat's side
# ---------------------------------------------------------------------------

def test_the_chat_has_a_tool_for_it():
    """The failure was never the data. It was that no tool could
    see the data, so the model reported its own blind spot as the
    project's."""
    from server.ai import strategist as st

    assert "when_you_post" in st.TOOLS
    schema = next(s for s in st.TOOL_SCHEMAS
                  if s["name"] == "when_you_post")
    assert schema["input_schema"]["properties"]["weekday"]["enum"] == list(
        tm.WEEKDAYS)
    # The tool's description has to make the model repeat the two
    # limits, because a bare "afternoon" answer is the wrong one.
    assert "not a recommendation" in schema["description"]
    assert "other creators" in schema["description"]


def test_an_hour_outside_the_clock_is_ignored_not_banded(monkeypatch):
    monkeypatch.setattr(tm, "build", lambda session: {
        "parts_of_day": [], "days_of_week": []})
    out = tm.advice(None, hour=99)
    assert "part_of_day" not in out["you_asked_about"]


def test_a_weekday_is_matched_case_insensitively(monkeypatch):
    monkeypatch.setattr(tm, "build", lambda session: {
        "parts_of_day": [],
        "days_of_week": [{"when": "Monday", "publications": 30}]})
    out = tm.advice(None, weekday="monday")
    assert out["you_asked_about"]["weekday"] == "Monday"
    assert out["you_asked_about"]["your_record_that_day"][
        "publications"] == 30


def test_a_weekday_that_is_not_one_does_not_invent_a_day(monkeypatch):
    monkeypatch.setattr(tm, "build", lambda session: {
        "parts_of_day": [], "days_of_week": []})
    out = tm.advice(None, weekday="whenever")
    assert out["you_asked_about"]["weekday"] is None


def test_the_timestamps_are_read_in_his_zone_not_utc():
    """Stored aware in Europe/London. In British Summer Time a post
    at 23:30 local is 22:30 UTC, and banding the UTC hour would
    file his late evening as somebody else's.
    """
    local = dt.datetime(2026, 7, 1, 23, 30, tzinfo=tm.OWNER_ZONE)
    assert local.utcoffset() == dt.timedelta(hours=1)
    assert tm.part_of_day(local.hour) == "night"
    assert tm.part_of_day(local.astimezone(dt.timezone.utc).hour) == "evening"


def test_the_headline_reports_a_band_that_is_only_bad_news():
    """The first version had a clause for a band above his usual
    and none for one below, so the real corpus - where the only
    band that survives is morning, 0.36 deviations *worse* -
    produced "Across 562 publications, , after correcting...".
    Wrong way round for a project whose negative results are its
    strongest."""
    head = tm._headline(
        [{"when": "morning", "median_vs_your_usual": -0.36}], 562)
    assert "morning ran below your usual" in head
    assert ", ," not in head


def test_the_headline_names_both_sides_when_there_are_both():
    head = tm._headline([
        {"when": "morning", "median_vs_your_usual": -0.36},
        {"when": "evening", "median_vs_your_usual": 0.4}], 562)
    assert "evening ran above your usual" in head
    assert "morning ran below it" in head


def test_the_headline_lists_several_bands_on_one_side():
    head = tm._headline([
        {"when": "morning", "median_vs_your_usual": -0.36},
        {"when": "night", "median_vs_your_usual": -0.2}], 562)
    assert "morning and night ran below your usual" in head


# ---------------------------------------------------------------------------
# the measurements that are labels, not amounts
# ---------------------------------------------------------------------------

def test_the_clock_is_never_rank_correlated():
    """`posted_hour_utc` was in the correlation pipeline as an
    ordinary number. Spearman puts 23:00 and 00:00 at opposite
    ends of a scale when they are an hour apart, so a real
    late-night effect cancels against a real early-morning one.
    `posted_weekday` is Monday = 0, which invents an ordering the
    week does not have.

    Neither ever appeared in a published finding - they did not
    clear the threshold - so nothing wrong was shown. One lucky q
    is not a safety margin.
    """
    from server.research import virality as vir

    assert "posted_hour_utc" in vir.NOT_A_SCALE
    assert "posted_weekday" in vir.NOT_A_SCALE
    # A genuine yes/no stays in, because it is one.
    assert "posted_is_weekend" not in vir.NOT_A_SCALE


def test_the_exclusion_applies_to_both_analyses():
    """`breakthroughs` is the worse of the two: it takes a mean,
    and the mean of 23:00 and 01:00 is noon."""
    import inspect

    from server.research import virality as vir

    assert "NOT_A_SCALE" in inspect.getsource(vir._build)
    assert "NOT_A_SCALE" in inspect.getsource(vir._breakthroughs)
