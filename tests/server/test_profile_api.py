"""The page that finally shows what the wide vision pass bought.

483 publications, 31 questions, 15,445 measurements - and until this
endpoint existed, none of it was visible anywhere in the product,
because every other surface reads a different method version. The
data sat in a table nobody queried and the product was exactly as
vague as it had been before the money was spent.

Two properties make this page honest rather than merely full, and
both are easy to lose:

1. **No outcome ever appears on it.** The moment a count sits beside
   a view count or a skip rate, a reader connects them. That
   connection belongs on "What to try next", where it arrives with a
   grade, a q-value and its counterexamples.
2. **The grade is on every row.** "55% show a face" rests on a
   measurement that cleared a bar set in advance; "35% are warmly
   lit" rests on a model's opinion nobody has checked. Same page,
   different epistemic status, and the page has to say so.
"""

from __future__ import annotations

import inspect

from server.ai import vision_questions as vq
from server.ai import wide_vision as wv
from server.api import profile_api as pa


def test_no_outcome_is_reachable_from_this_endpoint():
    """The structural guarantee. Not "we remembered not to" - the
    module does not import, query or mention any outcome."""
    src = inspect.getsource(pa)
    for forbidden in ("skip_rate", "reels_skip", "MetricsSnapshot",
                      "views", "retention", "_outcome_rows"):
        assert forbidden not in src, (
            f"{forbidden!r} appears in the visual profile. This page "
            f"describes what the videos ARE; the moment it can reach "
            f"an outcome somebody will put the two side by side.")


def test_unverified_measurements_are_not_shown_at_all():
    """`performer_visible` answered yes on 475 of 483 and
    `shot_distance` put 94% into two adjacent buckets. The wide run
    is what withheld them; printing them anyway would undo it."""
    src = inspect.getsource(pa.visual_profile)
    assert 'grade == "unverified"' in src
    assert "continue" in src
    for name in ("performer_visible", "shot_distance"):
        assert wv.reliability_of(f"vision_{name}") == "unverified", name


def test_every_shown_question_can_state_its_grade():
    """A page that mixes a checked measurement with a model's guess
    and does not say which is which is the quietly dishonest
    version of this feature."""
    for q in wv.ASKABLE:
        key = f"vision_{q.name}"
        grade = wv.reliability_of(key)
        if grade == "unverified":
            continue
        assert grade in ("verified", "indicative"), (q.name, grade)
        assert q.basis, f"{q.name} is shown with no basis to explain it"


def test_a_proportion_needs_enough_answers_to_be_one():
    """Below the floor a percentage is noise wearing a fact's
    clothes. `text_overlay_present` has exactly one negative in the
    own cohort, which is the kind of thing this stops."""
    assert pa.MIN_ANSWERS >= 20
    src = inspect.getsource(pa.visual_profile)
    assert "MIN_ANSWERS" in src


def test_interpretive_answers_are_flagged_as_readings():
    """Mood and formality have no ground truth, so they can never be
    more than the model's view however many videos agree."""
    interpretive = [q for q in wv.ASKABLE if q.interpretive]
    assert interpretive, "this guards a real case"
    for q in interpretive:
        assert wv.reliability_of(f"vision_{q.name}") != "verified", q.name
    assert "interpretive" in inspect.getsource(pa.visual_profile)


def test_the_page_explains_what_is_deliberately_missing():
    """Two questions are collected and hidden. A reader who notices
    the gap should find the reason rather than assume an oversight."""
    src = inspect.getsource(pa.visual_profile)
    assert "withheld" in src
    assert "475 of 483" in src


def test_the_per_publication_view_keeps_the_model_s_own_words():
    """The frame descriptions are the most readable thing the
    pipeline produces, and they are what lets a reader who disagrees
    with an answer see what the model thought it was looking at."""
    src = inspect.getsource(pa.publication_profile)
    assert "frame_descriptions" in src
    assert "what it thought it was looking at" in src


def test_a_publication_nobody_described_says_so():
    src = inspect.getsource(pa.publication_profile)
    assert '"described": False' in src
    assert "has not been described" in src


def test_the_method_version_is_reported():
    """Two models answering the same questions are two measurements.
    A page that did not say which produced its numbers would make
    them impossible to reconcile with anything."""
    assert "method_version" in inspect.getsource(pa.visual_profile)


def test_the_grouping_comes_from_the_question_set():
    """So a new question appears on the page by being declared, not
    by someone remembering to add it here too."""
    src = inspect.getsource(pa.visual_profile)
    assert "vq.GROUPS" in src
    assert len(vq.GROUPS) >= 5
