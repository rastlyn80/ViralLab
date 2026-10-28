"""Asking thirty-one questions per video instead of six.

The 2026-10-27 benchmarks settled two things. A frontier model does
not have the small model's failure - answering from the genre rather
than the frame - and asking is nearly free while checking is not,
because the images dominate the input and thirty more answers are a
few hundred output tokens.

So this stage asks widely and claims nothing. The tests guard the
three ways that goes wrong:

1. **The prompt drifting from the question list.** Schema, prompt and
   parser are all generated from `vision_questions.ALL`. A
   hand-maintained copy beside a declared list is two sources of
   truth and the one that runs is whichever the caller reached for.
2. **An invented answer being stored.** A value outside a question's
   vocabulary, or a field nobody declared, is indistinguishable from
   a measurement once it is a row in `video_features`.
3. **Breadth being mistaken for evidence.** Describing more videos
   better produces better descriptions. It promotes nothing, and the
   reliability grade has to travel with every row so that nothing
   downstream has to remember.
"""

from __future__ import annotations

import json

import pytest

from server.ai import vision_questions as vq
from server.ai import wide_vision as wv


# ---------------------------------------------------------------------------
# generated, not written twice
# ---------------------------------------------------------------------------

def test_the_question_set_is_wider_than_what_it_replaces():
    """Six booleans went in. If this drops back near six, the point
    of the exercise has been undone."""
    assert len(wv.ASKABLE) >= 25


def test_only_questions_with_an_extractor_are_asked():
    for q in wv.ASKABLE:
        assert "Not built" not in q.basis, q.name
        assert q.method in ("detector", "vlm"), q.name


def test_deterministic_measurements_are_not_asked_of_a_model():
    """Brightness and edge density are arithmetic on pixels. Asking a
    language model for them would be slower, worse, and would quietly
    create a second answer to a question that already has one."""
    names = {q.name for q in wv.ASKABLE}
    for q in vq.ALL:
        if q.method == "deterministic":
            assert q.name not in names, q.name


def test_every_asked_question_appears_in_the_prompt_and_the_schema():
    p, s = wv.prompt(), wv.schema()
    for q in wv.ASKABLE:
        assert f'"{q.name}"' in p, q.name
        assert q.name in s["properties"], q.name


def test_every_enum_question_publishes_its_vocabulary_in_the_prompt():
    """A closed vocabulary the model is never shown is a closed
    vocabulary the model will miss."""
    p = wv.prompt()
    for q in wv.ASKABLE:
        if q.answer == "enum":
            for v in q.values:
                assert v in p, f"{q.name}: {v}"


def test_the_prompt_carries_the_rules_that_were_learned_the_hard_way():
    p = wv.prompt().lower()
    # The genre shortcut that cost 79 of 80 publications.
    assert "not from the genre" in p or "genre" in p
    # The scope rule whose absence sent the owner's first label review
    # back for a second pass.
    assert "any of the frames" in p
    # The distinction two models and a labeller all got wrong.
    assert "the keys are visible" in p
    # Added after the owner's review: a painted face is not a person.
    assert "painted" in p


def test_the_prompt_asks_for_descriptions_before_answers():
    """Measured: on a model strong enough to use its own description
    this rescued on-screen-text recall from 0.53 to 0.89."""
    p = wv.prompt()
    assert p.index("STEP 1") < p.index("STEP 2")
    assert "frame_descriptions" in wv.schema()["properties"]


# ---------------------------------------------------------------------------
# nothing invented reaches the table
# ---------------------------------------------------------------------------

def test_an_undeclared_field_is_discarded():
    out = wv.parse(json.dumps({"face_visible": True,
                               "invented_field": "hello"}))
    assert out["face_visible"] is True
    assert "invented_field" not in out


def test_an_enum_answer_outside_its_vocabulary_is_discarded():
    """Storing "cathedral" where the vocabulary says church would put
    a value in `video_features` that no consumer can interpret and
    every consumer will treat as data."""
    out = wv.parse(json.dumps({"setting": "cathedral",
                               "camera_angle": "front"}))
    assert "setting" not in out
    assert out["camera_angle"] == "front"


def test_a_boolean_question_refuses_a_string():
    out = wv.parse(json.dumps({"face_visible": "yes"}))
    assert "face_visible" not in out


def test_a_truncated_reply_is_salvaged_rather_than_thrown_away():
    """Models truncate. A parser that only accepted a clean object
    discarded whole publications in the first benchmark run, which
    looked like refusals and was not."""
    broken = ('{"frame_descriptions": ["a long sentence about a man at '
              'a grand piano in a concert hall"], "face_visible": true, '
              '"hands_visible": false, "setting": "conce')
    out = wv.parse(broken)
    assert out["face_visible"] is True
    assert out["hands_visible"] is False
    assert out.get("_truncated") is True


def test_nothing_parseable_returns_empty_rather_than_guessing():
    assert wv.parse("I'm sorry, I can't help with that.") == {}
    assert wv.parse("") == {}


def test_descriptions_are_capped_so_one_reply_cannot_fill_a_column():
    out = wv.parse(json.dumps(
        {"frame_descriptions": ["x" * 2000] * 20}))
    assert len(out["frame_descriptions"]) <= wv.MAX_FRAMES
    assert all(len(d) <= 400 for d in out["frame_descriptions"])


# ---------------------------------------------------------------------------
# breadth is not evidence
# ---------------------------------------------------------------------------

def test_stored_names_match_the_namespace_the_rest_of_the_system_reads():
    rows = wv.feature_rows({"face_visible": True, "setting": "studio"})
    assert set(rows) == {"vision_face_visible", "vision_setting"}


def test_the_reliability_grade_travels_with_every_row():
    """So nothing downstream has to look it up to know whether a row
    may support a claim."""
    assert wv.reliability_of("vision_face_visible") == "verified"
    assert wv.reliability_of("vision_camera_angle") == "indicative"
    # Measured on Haiku against the owner-corrected labels after the
    # 2026-10-28 wide run: good enough to show as a reading, and a
    # separate test asserts it still cannot be counted.
    assert wv.reliability_of("vision_piano_or_keyboard_visible") == \
        "indicative"
    # Still unverified, and the wide run is why: 475 of 483, which is
    # the saturation signature of the original failure.
    assert wv.reliability_of("vision_performer_visible") == "unverified"


def test_an_unknown_feature_is_unverified_rather_than_assumed_fine():
    assert wv.reliability_of("vision_something_new") == "unverified"


def test_running_a_better_model_promotes_nothing():
    """The whole point. A wide pass produces descriptions; only the
    prospective holdout can turn one into a claim."""
    from server.features import visual_structure as vs
    for q in wv.ASKABLE:
        key = f"vision_{q.name}"
        if wv.reliability_of(key) != "verified":
            assert key not in set(vs.VISION_FEATURES), (
                f"{key} is not verified but is allowed into an analysis")


def test_the_method_version_names_the_model_it_was_produced_by():
    """Two models answering the same questions are two measurements,
    and a shared version string would let them be pooled."""
    assert "haiku" in wv.METHOD_VERSION
    assert wv.METHOD_VERSION != "hook-vision-gemma3-4b-v2"


@pytest.mark.parametrize("q", [q for q in wv.ASKABLE if q.answer == "enum"])
def test_every_enum_offers_an_escape_hatch(q):
    """A closed vocabulary with no way out forces a guess, and a
    forced guess is indistinguishable from an answer."""
    assert "unknown" in q.values, q.name
