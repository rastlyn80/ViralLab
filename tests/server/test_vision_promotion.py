"""The promotion gate: a vision field may only be used if it was measured.

M3's rule was written down before anything was scored - **specificity
>= 0.80 and lift >= +0.10 on the held-out set** - and the held-out set
is the 80 publications in the two fixtures here. These tests re-derive
the verdict from those fixtures and the stored arm outputs where they
are available, and otherwise assert the structural invariants that stop
a field drifting into analysis without evidence.

Why this file exists at all: `run_detect` was written as a pipeline
stage before it was measured, failed, and was removed the same day. The
expensive mistake in this project is not a wrong number, it is a field
that quietly becomes trusted. `VISION_FEATURES` is the list of fields
allowed to enter an analysis, so it is the place to enforce that.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.ai import vision_task
from server.features import visual_structure as vs

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "vision"
OWN = FIXTURES / "holdout_labels_own.json"
COMPETITOR = FIXTURES / "holdout_labels_competitor.json"

#: Measured 2026-10-27 on the pooled held-out set under the production
#: prompt, by scripts/provision/score_m3_arms.py. Recorded here so a
#: change to the promoted list has to confront the number that
#: justified it.
HELD_OUT_CONTROL_ARM = {
    "vision_text_overlay_present": {"specificity": 0.917, "lift": 0.138},
    "vision_face_visible": {"specificity": 0.808, "lift": 0.175},
}


def _labels(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_every_promoted_field_has_a_recorded_held_out_measurement():
    """No field may sit in VISION_FEATURES without one.

    A field with no entry here is a field someone added without
    measuring, which is the whole failure mode this gate exists for.
    """
    for name in vs.VISION_FEATURES:
        assert name in HELD_OUT_CONTROL_ARM, (
            f"{name} is promoted but has no held-out measurement recorded")


def test_every_promoted_field_actually_clears_the_preregistered_bar():
    for name, m in HELD_OUT_CONTROL_ARM.items():
        if name not in vs.VISION_FEATURES:
            continue
        assert m["specificity"] >= 0.80, (name, m)
        assert m["lift"] >= 0.10, (name, m)


def test_promoted_and_withheld_are_disjoint():
    """A field cannot be both usable and withheld; the two lists are read
    by different call sites and a field in both would behave differently
    depending on which one looked."""
    overlap = set(vs.VISION_FEATURES) & set(vs.WITHHELD_VISION)
    assert not overlap, overlap


def test_the_fields_that_failed_are_named_and_carry_their_reason():
    """Withholding silently is as bad as promoting silently - the stored
    rows stay visible in the interface, so the reason has to travel with
    them."""
    for name in ("vision_piano_or_keyboard_visible", "vision_hands_visible",
                 "vision_sheet_music_visible", "vision_shot_distance",
                 "vision_setting", "vision_performer_visible"):
        assert name in vs.WITHHELD_VISION, name
        reason = vs.WITHHELD_VISION[name]
        assert len(reason) > 40, f"{name}: reason too thin to be evidence"
        assert any(tok in reason for tok in
                   ("specificity", "accuracy", "lift", "no negatives",
                    "negatives")), f"{name}: reason cites no measurement"


def test_sheet_music_stays_withheld_despite_the_prompt_result():
    """The negative-example prompt takes it to specificity 0.807, which
    clears the bar - under a prompt the pipeline does not run, and whose
    adoption costs the two fields that are promoted. Promoting the field
    without switching the prompt would attribute a score to rows that
    were never produced that way."""
    assert "vision_sheet_music_visible" not in vs.VISION_FEATURES
    assert "vision_sheet_music_visible" in vs.WITHHELD_VISION


def test_the_pipeline_runs_the_prompt_the_promotions_were_measured_under(
        monkeypatch):
    """The link between the two is the whole argument.

    `face_visible` and `text_overlay_present` clear the bar under the
    control prompt and NOT under the treatment one. If the default
    variant ever changes, these promotions stop being supported by any
    measurement - so the default is asserted here rather than left as a
    comment in another module.
    """
    monkeypatch.delenv("VIRALLAB_VISION_PROMPT", raising=False)
    assert vision_task.DEFAULT_VARIANT == "original"
    assert vision_task.active_variant() == "original"


# ---------------------------------------------------------------------------
# the held-out fixtures themselves
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [OWN, COMPETITOR])
def test_a_held_out_fixture_declares_who_labelled_it_and_when(path):
    """These labels decide what the project is allowed to believe. Who
    wrote them is part of the evidence, not metadata."""
    doc = _labels(path)
    assert doc["_labelled_by"]
    assert "before any inference" in doc["_ordering_disclosure"].lower()
    assert len(doc["labels"]) == 40


def test_the_two_halves_do_not_overlap_each_other_or_the_tuning_set():
    """The 24 tuning publications have been used to disqualify two
    approaches. Any leak of them into the held-out set would make the
    held-out set measure memorisation."""
    own = set(_labels(OWN)["labels"])
    comp = set(_labels(COMPETITOR)["labels"])
    tuning = set(_labels(FIXTURES / "human_labels_v2.json")["labels"])
    assert not own & comp
    assert not own & tuning
    assert not comp & tuning


@pytest.mark.parametrize("path", [OWN, COMPETITOR])
def test_categorical_labels_stay_inside_the_task_enums(path):
    """A label outside the enum is unanswerable: the grammar cannot emit
    it, so every model answer scores wrong and the field looks worse
    than it is. Where no enum value fits, the fixture omits the field.
    """
    doc = _labels(path)["labels"]
    for vid, row in doc.items():
        if "setting" in row:
            assert row["setting"] in vision_task.SETTING, (vid, row["setting"])
        if "shot_distance" in row:
            assert row["shot_distance"] in vision_task.SHOT_DISTANCE, (
                vid, row["shot_distance"])


@pytest.mark.parametrize("path", [OWN, COMPETITOR])
def test_every_flagged_uncertain_case_points_at_a_real_label(path):
    doc = _labels(path)
    for row in doc["_uncertain"]:
        vid = str(row["video_id"])
        assert vid in doc["labels"], vid
        # The field may legitimately be one the fixture omits - that is
        # itself a recorded judgement - so only the publication must
        # exist, not the key.
        assert row["why"]


def test_the_competitor_half_supplies_the_negatives_the_own_half_cannot():
    """This is the reason the competitor half was worth $0.16.

    `performer_visible` has no negatives at all on the own half, so it
    is unmeasurable there; the claim that the competitor half fixes
    that is checked rather than asserted in prose.
    """
    own = _labels(OWN)["labels"]
    comp = _labels(COMPETITOR)["labels"]
    own_neg = sum(1 for r in own.values() if not r["performer_visible"])
    comp_neg = sum(1 for r in comp.values() if not r["performer_visible"])
    assert own_neg == 0
    assert comp_neg >= 4

    own_txt = sum(1 for r in own.values() if not r["text_overlay_present"])
    comp_txt = sum(1 for r in comp.values() if not r["text_overlay_present"])
    assert own_txt <= 1
    assert comp_txt >= 8
