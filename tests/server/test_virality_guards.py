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
    442 rows and 3 cases."""
    assert vir.MIN_BOOLEAN_CASES >= 10
    src = inspect.getsource(vir.breakthroughs)
    assert "min(trues, falses)" in src
    assert "MIN_BOOLEAN_CASES" in src


def test_the_outlier_side_needs_cases_too():
    """A feature true on 40 publications overall but on none of the
    outliers still has nothing to say about outliers."""
    src = inspect.getsource(vir.breakthroughs)
    assert "sum(a) < 3" in src


def test_the_guards_are_documented_where_they_bite():
    """A numeric floor with no reason beside it gets raised by the
    next person who finds it inconvenient."""
    src = inspect.getsource(vir)
    assert "3 publications EACH out of 442" in src
    assert "two videos" in src
