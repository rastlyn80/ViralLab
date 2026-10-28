"""The guards that stop a small p-value on nothing becoming advice.

Two of them, both added after the analysis produced a number that
read as a finding and was not.

`TIME_PROXIES` exists because `publications_before_this` cleared
correction on both sides - positive on the owner's account, negative
on competitors'. A property of a video does not reverse; a count of
elapsed time does, depending on whether the account was growing.

`MIN_BOOLEAN_CASES` exists because `hook_is_what_if` and
`hook_names_celebrity` are each true on 3 publications out of 442,
and both came out at p = 0.011 against breaking out. "6% of
breakthroughs used a what-if hook" is two videos. A rank test cannot
tell that two is not a sample.
"""

from __future__ import annotations

import inspect

from server.research import virality as vir


def test_a_count_of_elapsed_time_is_not_a_content_finding():
    assert "publications_before_this" in vir.TIME_PROXIES
    assert "is_first_post" in vir.TIME_PROXIES
    src = inspect.getsource(vir.build)
    assert "account_trends" in src, (
        "time proxies must still be reported, just not as findings")


def test_time_proxies_are_excluded_from_breakthrough_differences():
    src = inspect.getsource(vir.breakthroughs)
    assert "TIME_PROXIES" in src


def test_a_rare_boolean_cannot_produce_a_difference():
    """The floor is on the rarer answer, not on the row count: a
    feature measured on 442 publications and true on 3 of them has
    442 rows and 3 cases.

    Checked by running the rule rather than by reading the source.
    The previous version of this test asserted that the string
    "min(trues, falses)" appeared in `breakthroughs`, and it passed
    for months while that line was **unreachable** - a guard above
    it dropped every boolean before it could be applied. A test that
    reads source proves the code is present; only running it proves
    the code runs.
    """
    assert vir.MIN_BOOLEAN_CASES >= 10
    rare_a = [1.0] * 2 + [0.0] * 30
    rare_b = [1.0] * 1 + [0.0] * 200
    assert vir.why_not_testable(rare_a, rare_b) != ""


def test_the_outlier_side_needs_cases_too():
    """A feature true on 40 publications overall but on almost none
    of the outliers still has nothing to say about outliers."""
    a = [1.0] * 2 + [0.0] * 40
    b = [1.0] * 40 + [0.0] * 40
    assert "breakthroughs" in vir.why_not_testable(a, b)


def test_a_healthy_boolean_is_not_caught_by_the_rare_one_guard():
    """The other half, which nothing checked and which was broken.

    Guarding against a rare boolean must not become guarding
    against every boolean. It did, and the cost was 43 of 103
    measurements never being tested at all.
    """
    a = [1.0] * 10 + [0.0] * 10
    b = [1.0] * 30 + [0.0] * 30
    assert vir.why_not_testable(a, b) == ""


def test_the_guards_are_documented_where_they_bite():
    """A numeric floor with no reason beside it gets raised by the
    next person who finds it inconvenient."""
    src = inspect.getsource(vir)
    assert "3 publications EACH out of 442" in src
    assert "two videos" in src
