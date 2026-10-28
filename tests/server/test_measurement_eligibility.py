"""Which measurements are allowed to be compared, and why not.

This file exists because of a bug that produced no error, no log
line and no wrong number - it produced *absence*. For the whole life
of the analysis, the eligibility test was:

    if len(a) < 8 or len(b) < floor_b or len(set(a + b)) < 3:
        continue

and a boolean has exactly two distinct values. Every yes/no
measurement in the project failed it: eleven hook devices, every
caption shape, whether the music was his own, twenty vision
questions. 43 of 103 measurements, never once tested. The
boolean-specific rule written immediately underneath was unreachable
code and so never caught anything either.

Nothing could have caught this by reading output, because **a
measurement that is never tested looks exactly like one that was
tested and found nothing**. So the rule is a named function with a
reason string, and these tests pin each branch of it.
"""

from __future__ import annotations

from server.research import virality as vir


def test_a_boolean_is_recognised_as_one():
    assert vir.is_boolean([0.0, 1.0, 1.0, 0.0])
    assert vir.is_boolean([1.0] * 5)
    assert vir.is_boolean([])
    assert not vir.is_boolean([0.0, 0.5, 1.0])
    assert not vir.is_boolean([2.0, 3.0])


def test_the_bug_itself_a_healthy_boolean_is_testable():
    """The regression. This returned "too few distinct values"."""
    # 20 breakthroughs, 60 others, both values well represented.
    a = [1.0] * 10 + [0.0] * 10
    b = [1.0] * 30 + [0.0] * 30
    assert vir.why_not_testable(a, b) == ""


def test_a_rare_boolean_is_still_refused():
    """`hook_is_what_if`: true on 3 of 442, p = 0.011, meaningless."""
    a = [1.0] * 2 + [0.0] * 20
    b = [1.0] * 1 + [0.0] * 100
    why = vir.why_not_testable(a, b)
    assert "smaller side" in why


def test_a_boolean_true_on_too_few_breakthroughs_is_refused():
    # Plenty on both sides overall, but almost none among the hits.
    a = [1.0] * 2 + [0.0] * 40
    b = [1.0] * 40 + [0.0] * 40
    assert "breakthroughs" in vir.why_not_testable(a, b)


def test_a_constant_boolean_is_refused():
    assert vir.why_not_testable([1.0] * 20, [1.0] * 60) != ""
    assert vir.why_not_testable([0.0] * 20, [0.0] * 60) != ""


def test_a_nearly_constant_continuous_feature_is_still_refused():
    """The rule the boolean guard was standing in front of."""
    a = [0.5] * 10
    b = [0.5] * 50 + [0.7] * 10
    assert "distinct values" in vir.why_not_testable(a, b)


def test_a_real_continuous_feature_is_testable():
    a = [i / 10 for i in range(10)]
    b = [i / 50 for i in range(60)]
    assert vir.why_not_testable(a, b) == ""


def test_too_few_on_either_side_is_refused_with_the_count():
    assert "8" in vir.why_not_testable([0.1] * 3, [0.2] * 60)
    why = vir.why_not_testable([i / 10 for i in range(10)], [0.2] * 5)
    assert "others measured" in why


def test_the_origin_split_uses_a_lower_floor():
    """Splitting by whose music it is leaves fewer on each side."""
    a = [i / 10 for i in range(10)]
    b = [i / 20 for i in range(20)]
    assert vir.why_not_testable(a, b, floor_b=30) != ""
    assert vir.why_not_testable(a, b, floor_b=15) == ""


def test_every_refusal_says_why():
    """A silent skip is what made the original bug invisible."""
    cases = [
        ([0.1] * 3, [0.2] * 60),
        ([i / 10 for i in range(10)], [0.2] * 5),
        ([1.0] * 20, [1.0] * 60),
        ([0.5] * 10, [0.5] * 60),
        ([1.0] * 2 + [0.0] * 20, [1.0] + [0.0] * 100),
    ]
    for a, b in cases:
        why = vir.why_not_testable(a, b)
        assert why and isinstance(why, str) and len(why) > 10, (a[:3], b[:3])
