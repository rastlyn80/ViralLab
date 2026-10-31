"""Other creators' best openings, and what may be learned from them.

The owner asked for an AI that can "access the best practices from
competitors' videos in terms of hooks, content". The generator had
his own openings verbatim and nothing from anyone else, so it could
rearrange his voice and never show him a shape he had not tried.

Two things decide whether this helps or misleads: how an example is
chosen, and whether debris gets through dressed as a hook.
"""

from __future__ import annotations

from server.ai import exemplars as ex


def test_an_example_is_chosen_within_its_creator_not_by_views():
    """`pianoescu` has a video at 69 million.

    Sorting by views returns that account's catalogue and calls it
    best practice. The bar is how far a video beat its OWN
    creator's others.
    """
    assert 0.5 < ex.MIN_PERCENTILE < 1.0
    for stmt in (ex._OPENINGS, ex._CAPTIONS):
        sql = str(stmt)
        assert "percent_rank() over (partition by v.creator_id" in sql
        assert ":min_pr" in sql


def test_one_creator_cannot_supply_the_whole_list():
    """Otherwise "what works" becomes "what this person does"."""
    assert ex.MAX_PER_CREATOR <= 3
    for stmt in (ex._OPENINGS, ex._CAPTIONS):
        assert "rank_in_creator <= :max_per" in str(stmt)


def test_a_thin_creator_cannot_contribute():
    """A percentile among three videos means nothing."""
    from server.research import virality as vir
    assert ex.MIN_PER_CREATOR == vir.MIN_PER_CREATOR


def test_a_chord_chart_is_not_a_hook():
    """It was the second-best-performing opening in the corpus."""
    assert not ex._is_language("Eb / Ab / AD / Bb / AD / Ab / Eb / Ab")
    assert not ex._is_language("C / Am / F / G / C / Am")


def test_a_block_of_hashtags_is_not_a_caption():
    assert not ex._is_language("#piano #pianotok #fyp #goldenbrown")
    assert not ex._is_language("@plaga5 @thegeorgianterrace #piano #fyp")


def test_a_real_opening_survives():
    for good in ("Everybody thinks they are impressive playing this Piece",
                 "Piano without vs with pedal. Without pedal",
                 "When I knew she was a born musician",
                 "How to get kicked out of a 5 star hotel"):
        assert ex._is_language(good), good


def test_something_too_short_to_learn_from_is_dropped():
    assert not ex._is_language("nice one")
    assert not ex._is_language("")


def test_mostly_non_latin_is_dropped_without_judging_it():
    """Real captions, and not ones this owner can learn phrasing
    from. Dropped for usefulness, not for worth."""
    assert not ex._is_language(
        "\u042d\u0442\u043e \u043c\u043e\u044f \u043b\u044e\u0431\u0438"
        "\u043c\u0430\u044f \u043f\u0435\u0441\u043d\u044f \u0441\u0435"
        "\u0433\u043e\u0434\u043d\u044f")


def test_repeated_frames_are_collapsed_but_order_is_kept():
    """The order is the hook."""
    out = ex._tidy("Hall3 | Hall3 | Hall2 | Hall2 | The most EPIC | "
                   "PIANO BATTLE")
    assert out == "Hall3 / Hall2 / The most EPIC / PIANO BATTLE"


def test_the_rule_about_copying_travels_with_the_examples():
    """Stated in the data, not only in the prompt."""
    note = ex.gather.__doc__ or ""
    src = ex.__doc__ or ""
    assert "never" in src.lower() and "shape" in src.lower()
