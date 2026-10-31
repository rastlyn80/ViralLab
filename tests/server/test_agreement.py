"""Agreement means the same direction, not the same name.

The strongest claim this product makes is that a measurement held
on the owner's account AND independently on other creators. It was
computed by intersecting NAMES, so caption length - negative on his
account, positive on 291 others - was reported as an agreement.
Two of the first eight were contradictions presented as the best
evidence available.

A contradiction is worth seeing. It usually means the pattern is
about this creator rather than about piano videos, which is
exactly what a reader needs before acting on it.
"""

from __future__ import annotations

import inspect

from server.research import virality as vir


def test_the_two_analyses_speak_the_same_words():
    """`build` says positive/negative; `breakthroughs` says
    higher/lower. Comparing them needs one vocabulary."""
    assert vir._direction_of({"direction": "positive"}) == "higher"
    assert vir._direction_of({"direction": "negative"}) == "lower"
    assert vir._direction_of({"direction": "higher"}) == "higher"
    assert vir._direction_of({"direction": "lower"}) == "lower"


def test_the_same_direction_is_an_agreement():
    agree, against = vir.directional_overlap(
        [{"display_name": "A composer is named", "direction": "positive"}],
        [{"display_name": "A composer is named", "direction": "positive"}])
    assert agree == ["A composer is named"]
    assert against == []


def test_opposite_directions_are_not_an_agreement():
    """The live case: caption length, negative for him, positive
    for 291 other creators."""
    agree, against = vir.directional_overlap(
        [{"display_name": "Caption length", "direction": "negative"}],
        [{"display_name": "Caption length", "direction": "positive"}])
    assert agree == []
    assert against == [{"display_name": "Caption length",
                        "yours": "lower", "theirs": "higher"}]


def test_the_two_vocabularies_can_be_compared():
    """A breakthrough difference and a gradient finding about the
    same thing must not look like a disagreement because one says
    "higher" and the other says "positive"."""
    agree, against = vir.directional_overlap(
        [{"display_name": "Sheet music", "direction": "higher"}],
        [{"display_name": "Sheet music", "direction": "positive"}])
    assert agree == ["Sheet music"]
    assert against == []


def test_a_measurement_on_one_side_only_is_neither():
    agree, against = vir.directional_overlap(
        [{"display_name": "Only mine", "direction": "positive"}],
        [{"display_name": "Only theirs", "direction": "positive"}])
    assert agree == [] and against == []


def test_every_caller_uses_the_shared_rule():
    """Three surfaces make this claim and one copy per surface is
    how two of them start disagreeing about what agreement means."""
    from server.ai import creative, prepublish
    from server.api import virality_api

    for module in (creative.build_brief, prepublish._targets):
        assert "directional_overlap" in inspect.getsource(module), module
    assert "directional_overlap" in inspect.getsource(virality_api)
