"""The question set, and the three ways it could quietly become a lie.

`vision_questions.py` widens the visual vocabulary from twenty
questions to forty-two and introduces a reliability grade so that a
measurement can be shown to a person without being allowed to support
a statistic. That is useful and it is also three new ways to be wrong:

1. **A quoted figure drifts from the data.** Every specificity and
   lift in a `basis` string is recomputed here from the committed
   benchmark. Three of them were wrong when first written from
   memory - a field's lift quoted as +0.142 when it measured +0.175,
   and a specificity quoted as 0.955 when it measured 0.917 - which is
   precisely why this is a test and not a convention.

2. **The grade stops matching the system.** A question marked
   `verified` here that is not actually allowed into an analysis by
   `visual_structure.VISION_FEATURES`, or an `unverified` one that is,
   means two files disagree about what is trusted and the stricter one
   is not necessarily the one that runs.

3. **`indicative` starts being treated as evidence.** The whole value
   of the middle grade is that it shows a reading without claiming it
   is measured. The moment something indicative can enter a
   statistic, the grade has bought nothing and cost a false sense of
   rigour.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from server.ai import vision_questions as vq
from server.features import visual_structure as vs

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "vision"
COMPARISON = FIXTURES / "detector_comparison.json"

#: `visual_structure` prefixes the VLM fields; `vision_questions`
#: names the question. One prefix, applied to the model-answered ones.
PREFIX = "vision_"


def _pooled(approach: str, field: str) -> dict | None:
    d = json.loads(COMPARISON.read_text(encoding="utf-8"))
    return (d["approaches"].get(approach, {}).get("fields", {})
            .get(field, {}).get("in_sample_optimistic"))


# ---------------------------------------------------------------------------
# 1. quoted figures must match the committed benchmark
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,approach", [
    ("face_visible", "gemma"),
    ("text_overlay_present", "gemma"),
])
def test_quoted_figures_match_the_measurement(name, approach):
    """The basis string is prose, so nothing stops it drifting except
    this. Pull the numbers back out of it and compare."""
    q = vq.by_name(name)
    assert q is not None and q.basis
    measured = _pooled(approach, name)
    assert measured is not None, f"no committed figure for {name}"

    spec = re.search(r"([0-9]\.[0-9]{3}) specificity", q.basis)
    lift = re.search(r"\+([0-9]\.[0-9]{3}) lift", q.basis)
    assert spec, f"{name}: basis quotes no specificity"
    assert lift, f"{name}: basis quotes no lift"
    assert float(spec.group(1)) == round(measured["specificity"], 3)
    assert float(lift.group(1)) == round(measured["lift"], 3)


def test_every_non_unverified_question_says_why():
    """A grade without a reason is an assertion. The interface shows
    the basis next to the answer, so an empty one is a blank claim."""
    for q in vq.ALL:
        if q.reliability != "unverified":
            assert q.basis, f"{q.name} is {q.reliability} with no basis"


# ---------------------------------------------------------------------------
# 2. the grade must match the system that actually runs
# ---------------------------------------------------------------------------

def test_verified_model_questions_are_the_ones_analysis_allows():
    """`VISION_FEATURES` is the list the pipeline honours. If this file
    says verified and that file does not, this file is decoration."""
    allowed = set(vs.VISION_FEATURES)
    for q in vq.ALL:
        if q.method not in ("vlm", "detector"):
            continue                      # arithmetic, not in that list
        key = PREFIX + q.name
        if q.reliability == "verified":
            assert key in allowed, (
                f"{q.name} is marked verified but {key} is not in "
                f"VISION_FEATURES, so nothing may actually use it")
        else:
            assert key not in allowed, (
                f"{q.name} is {q.reliability} but {key} IS allowed into "
                f"an analysis. The stricter grade is not the one running.")


def test_everything_withheld_by_the_pipeline_is_unverified_here():
    for key in vs.WITHHELD_VISION:
        name = key[len(PREFIX):] if key.startswith(PREFIX) else key
        q = vq.by_name(name)
        if q is None:
            continue                      # withheld and not re-asked
        assert q.reliability == "unverified", (
            f"{name} is withheld by the pipeline but marked "
            f"{q.reliability} here")


# ---------------------------------------------------------------------------
# 3. indicative must never become evidence
# ---------------------------------------------------------------------------

def test_indicative_can_be_shown_and_cannot_be_counted():
    shown = [q for q in vq.ALL if q.reliability == "indicative"]
    assert shown, "the middle grade exists to be used"
    for q in shown:
        assert vq.may_be_shown(q.name)
        assert not vq.may_enter_statistics(q.name), (
            f"{q.name} is indicative and may enter a statistic. The "
            f"grade has bought nothing.")


def test_unverified_is_neither_shown_nor_counted():
    for q in vq.at("unverified"):
        assert not vq.may_be_shown(q.name)
        assert not vq.may_enter_statistics(q.name)


def test_an_interpretation_can_never_be_verified():
    """Mood and intent have no ground truth, so there is nothing a
    holdout could check them against. Allowing one to reach verified
    would mean a number existed that could not have been measured."""
    for q in vq.ALL:
        if q.interpretive:
            assert q.reliability != "verified", q.name


def test_a_question_nobody_built_cannot_be_trusted():
    built = {q.name for q in vq.buildable_today()}
    for q in vq.ALL:
        if q.name not in built:
            assert q.reliability == "unverified", (
                f"{q.name} has no extractor and is graded {q.reliability}")


# ---------------------------------------------------------------------------
# what the next collection pass has to contain
# ---------------------------------------------------------------------------

def test_the_fields_phase_1_could_not_judge_demand_negatives():
    """Phase 1's finding, carried forward as a requirement rather than
    a paragraph. If these lose their `needs_negatives` note, the next
    holdout will be built without them and reproduce the dead end."""
    required = vq.holdout_requirements()
    for name in ("piano_or_keyboard_visible", "hands_visible",
                 "text_overlay_present"):
        assert name in required, (
            f"{name} could not be measured on the M3 corpus for want of "
            f"negatives; the holdout spec must still say so")
        assert len(required[name]) > 40, (
            f"{name}: 'needs negatives' is not an instruction. Say what "
            f"the negative has to look like.")


def test_the_question_set_is_actually_wider_than_what_it_replaces():
    """The point of the exercise. Twenty questions went in; if this
    ever drops back near twenty, the widening has been undone."""
    assert len(vq.ALL) >= 40
    assert len(vq.at("indicative")) >= 6, (
        "the twelve orphan fields were the immediate win: extracted on "
        "every publication since M3 and shown to nobody")


def test_names_are_unique():
    names = [q.name for q in vq.ALL]
    assert len(names) == len(set(names))


def test_every_enum_question_offers_an_unknown():
    """A closed vocabulary with no escape hatch forces a guess, and a
    forced guess is indistinguishable from an answer."""
    for q in vq.ALL:
        if q.answer == "enum" and q.method in ("vlm", "detector"):
            assert "unknown" in q.values, q.name


def test_every_detector_question_carries_more_than_one_phrasing():
    """A single text query is a lottery - Phase 1 measured OWLv2
    swinging on phrasing alone."""
    for q in vq.ALL:
        if q.method == "detector":
            assert len(q.queries) >= 2, q.name
