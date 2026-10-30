"""Two of your videos that should have done the same, and did not.

The owner's own words, long before anything existed: find what
distinguishes "a video that is successful with respect to another
which is similar and draws no traffic". ADR 0011 makes this part of
the evidence base rather than a substitute for the experiment.

The pairing rules are where this can go quietly wrong - a pair that
is not really a pair produces a confident number about nothing - so
that is what these test.
"""

from __future__ import annotations

import datetime as dt
import types

from server.research import matched_pairs as mp


# ---------------------------------------------------------------------------
# the arithmetic
# ---------------------------------------------------------------------------

def test_the_sign_test_is_an_exact_two_sided_binomial():
    assert round(mp._sign_test_p(8, 0), 5) == 0.00781
    assert round(mp._sign_test_p(12, 0), 5) == 0.00049
    assert mp._sign_test_p(6, 6) == 1.0
    assert mp._sign_test_p(5, 4) == 1.0


def test_it_is_symmetric():
    """Direction is read off the counts, not off the p-value."""
    assert mp._sign_test_p(9, 2) == mp._sign_test_p(2, 9)


def test_no_pairs_is_not_a_p_value_of_one():
    assert mp._sign_test_p(0, 0) is None


# ---------------------------------------------------------------------------
# what counts as a pair
# ---------------------------------------------------------------------------

def _video(vid, *, platform="tiktok", days=0, duration=60.0, views=1000):
    return vid, {"posted_at": dt.datetime(2026, 1, 1) + dt.timedelta(days=days),
                 "caption": f"caption {vid}", "platform": platform,
                 "duration_s": duration, "views": views}


def _run(meta, z, desc, monkeypatch, **kwargs):
    monkeypatch.setattr(mp, "_population", lambda s: (meta, z))
    monkeypatch.setattr(mp, "_descriptors", lambda s, ids: desc)
    return mp.build_pairs(None, **kwargs)


def _desc(ids, origin="repertoire", recog=1.0):
    return {i: {"origin": origin, "recognisability": recog} for i in ids}


def test_two_videos_that_both_did_averagely_are_not_a_pair():
    """A pair has to contain a contrast or it teaches nothing."""
    assert mp.MIN_GAP_Z >= 1.0


def test_a_pair_needs_the_same_platform(monkeypatch):
    meta = dict([_video(1, platform="tiktok"),
                 _video(2, platform="instagram")])
    pairs = _run(meta, {1: 3.0, 2: -1.0}, _desc([1, 2]), monkeypatch)
    assert pairs == [], "TikTok and Instagram are different audiences"


def test_a_pair_needs_a_similar_length(monkeypatch):
    meta = dict([_video(1, duration=20.0), _video(2, duration=200.0)])
    pairs = _run(meta, {1: 3.0, 2: -1.0}, _desc([1, 2]), monkeypatch)
    assert pairs == [], "a reel and a performance are different products"


def test_a_pair_needs_the_same_kind_of_music(monkeypatch):
    meta = dict([_video(1), _video(2)])
    desc = {1: {"origin": "own_composition", "recognisability": 1.0},
            2: {"origin": "repertoire", "recognisability": 1.0}}
    assert _run(meta, {1: 3.0, 2: -1.0}, desc, monkeypatch) == []


def test_a_pair_needs_a_similar_piece_recognisability(monkeypatch):
    meta = dict([_video(1), _video(2)])
    desc = {1: {"origin": "repertoire", "recognisability": 1.0},
            2: {"origin": "repertoire", "recognisability": 3.0}}
    assert _run(meta, {1: 3.0, 2: -1.0}, desc, monkeypatch) == []


def test_a_pair_must_not_span_years(monkeypatch):
    meta = dict([_video(1, days=0), _video(2, days=900)])
    assert _run(meta, {1: 3.0, 2: -1.0}, _desc([1, 2]), monkeypatch) == []


def test_a_real_pair_is_found_and_the_winner_is_the_better_one(monkeypatch):
    meta = dict([_video(1, views=500), _video(2, days=30, views=9000)])
    pairs = _run(meta, {1: -1.0, 2: 3.0}, _desc([1, 2]), monkeypatch)
    assert len(pairs) == 1
    assert pairs[0].winner_id == 2
    assert pairs[0].loser_id == 1
    assert pairs[0].basis["days_apart"] == 30


def test_a_publication_is_used_at_most_once(monkeypatch):
    """Otherwise one freak video votes in every pair."""
    meta = dict([_video(1, views=50_000), _video(2, days=10),
                 _video(3, days=20), _video(4, days=30)])
    z = {1: 9.0, 2: -1.0, 3: -1.2, 4: -1.4}
    pairs = _run(meta, z, _desc([1, 2, 3, 4]), monkeypatch)
    used = [v for p in pairs for v in (p.winner_id, p.loser_id)]
    assert len(used) == len(set(used))
    assert sum(1 for p in pairs if 1 in (p.winner_id, p.loser_id)) == 1


def test_the_biggest_contrast_gets_its_partner_first(monkeypatch):
    meta = dict([_video(1), _video(2, days=5), _video(3, days=10)])
    z = {1: 5.0, 2: -3.0, 3: 0.0}
    pairs = _run(meta, z, _desc([1, 2, 3]), monkeypatch)
    assert {pairs[0].winner_id, pairs[0].loser_id} == {1, 2}


# ---------------------------------------------------------------------------
# what it refuses to say
# ---------------------------------------------------------------------------

def test_too_few_pairs_reports_why_instead_of_a_finding(monkeypatch):
    monkeypatch.setattr(mp, "build_pairs", lambda s, **k: [])
    out = mp.analyse(None)
    assert out["differences"] == []
    assert "alike enough" in out["why_empty"]


def test_it_never_claims_a_cause():
    how = mp._how_it_works()
    assert "never which difference did the work" in how["still_not_a_cause"]


def test_the_floor_leaves_room_for_correction_to_bite():
    """Eight pairs all one way is p = 0.008.

    Not "the smallest number that reaches 0.05" - seven all one way
    is already 0.016, and six is 0.031. The floor is higher than
    bare significance on purpose, because these p-values then go
    through correction for testing seventy-odd measurements, and a
    result that only just clears 0.05 uncorrected will not survive
    and should not be shown while it waits to be discarded.
    """
    assert mp.MIN_PAIRS >= 8
    assert mp._sign_test_p(mp.MIN_PAIRS, 0) < 0.01
