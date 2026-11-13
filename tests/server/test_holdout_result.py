"""The prospective holdout, scored - and the ways this could rot.

The numbers live in `holdout_result.json` and are written up in
`docs/benchmarks/7e-holdout-result.md`. This file stops the two
drifting apart, and stops three specific mistakes a later session
could make in good faith:

1. **Promoting a field because the specificity looks good.** Both
   fields under test clear the specificity bar. Neither clears lift,
   and the holdout promoted nothing. A reader skimming the first
   column would switch them on.
2. **Re-using a spent holdout.** It decided promotion on 2026-11-13
   and may not decide anything else.
3. **Keeping a lift bar that nothing can clear.** `hands_visible`
   cannot reach +0.10 on this corpus at any level of skill, because
   answering yes every time is already right 91.2% of the time. That
   fact is asserted here because it is the finding most likely to be
   forgotten and repeated.

The thresholds are checked against the M3 fixture they were taken
from, so "fixed beforehand on the spent corpus" stays true rather
than becoming a thing the protocol merely says.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.provision.score_holdout import (BAR_LIFT, BAR_SPECIFICITY,
                                             THRESHOLDS)
from server.features import visual_structure as vs

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "vision"
RESULT = FIXTURES / "holdout_result.json"
COMPARISON = FIXTURES / "detector_comparison.json"
REPORT = ROOT / "docs" / "benchmarks" / "7e-holdout-result.md"
PROTOCOL = ROOT / "docs" / "benchmarks" / "7b-holdout-protocol.md"

UNDER_TEST = ("piano_or_keyboard_visible", "hands_visible")


def _result() -> dict:
    return json.loads(RESULT.read_text(encoding="utf-8"))


def _arm(field: str, kind: str = "primary") -> dict:
    return _result()["fields"][field]["arms"][kind]["primary_analysis"]


# ---------------------------------------------------------------------------
# the design, as drawn
# ---------------------------------------------------------------------------

def test_the_bars_are_the_pre_registered_ones():
    assert _result()["bars"] == {"specificity": 0.80, "lift": 0.10}
    assert (BAR_SPECIFICITY, BAR_LIFT) == (0.80, 0.10)


def test_every_drawn_publication_was_answered():
    r = _result()
    assert r["n_publications"] == 111
    sampled = sum(d["sampled"] for d in r["design"])
    assert sampled == 111, "the scored set must be the drawn set"


def test_the_shortfall_against_the_planned_120_is_recorded():
    top = [d for d in _result()["design"] if d["stratum"] == 4][0]
    assert top["eligible"] == 15 and top["shortfall"] == 9


def test_the_weights_divide_the_enrichment_back_out():
    # 443 eligible publications behind 111 sampled ones.
    design = _result()["design"]
    assert sum(d["eligible"] for d in design) == 443
    for d in design:
        assert abs(d["weight"] - d["eligible"] / d["sampled"]) < 1e-3


# ---------------------------------------------------------------------------
# thresholds came from the spent corpus, and are checkably those
# ---------------------------------------------------------------------------

def test_thresholds_are_the_ones_the_m3_corpus_fixed():
    m3 = json.loads(COMPARISON.read_text(
        encoding="utf-8"))["approaches"]["owlv2_base"]["fields"]
    for field, choice in THRESHOLDS.items():
        for kind in ("primary", "secondary"):
            source, value = choice[kind]
            assert m3[field][source]["threshold"] == value, (
                f"{field} {kind} threshold must be the M3 cell it "
                f"claims to come from")


def test_no_in_sample_threshold_is_eligible():
    for choice in THRESHOLDS.values():
        for kind in ("primary", "secondary"):
            assert choice[kind][0] != "in_sample_optimistic"


def test_the_primary_threshold_never_saw_an_own_account_video():
    # The holdout is own-account, so the deciding threshold is the one
    # tuned on competitors. See protocol section 7.2.
    for choice in THRESHOLDS.values():
        assert choice["primary"][0] == "tuned_on_competitor_tested_on_own"


# ---------------------------------------------------------------------------
# the result
# ---------------------------------------------------------------------------

def test_the_holdout_promoted_nothing():
    for field, cell in _result()["fields"].items():
        assert cell["promoted"] is False, field
        assert cell["promotion_scope"] == "not promoted", field


def test_both_fields_clear_specificity_and_fail_on_lift():
    for field in UNDER_TEST:
        cell = _arm(field)
        assert cell["clears_specificity"] is True, field
        assert cell["clears_lift"] is False, field


def test_piano_specificity_is_finally_measurable():
    # The whole point of drawing a new holdout: 4 and 6 negatives
    # before, 27 now.
    cell = _arm("piano_or_keyboard_visible")
    assert cell["negatives"] == 27
    assert cell["specificity"] == 0.9259
    assert cell["specificity_ci95"] == [0.7663, 0.9794]
    assert cell["lift"] == 0.0595


def test_hands_has_perfect_specificity_and_negative_lift():
    cell = _arm("hands_visible")
    assert cell["negatives"] == 15
    assert cell["fp"] == 0 and cell["specificity"] == 1.0
    assert cell["lift"] == -0.0107
    assert cell["recall"] == 0.8043


def test_the_conclusion_survives_the_cannot_tell_answers():
    # Assigned adversarially, per protocol section 7.3. Nothing here
    # depends on how the 6 undecidable answers are treated.
    for field in UNDER_TEST:
        adv = _result()["fields"][field]["arms"]["primary"][
            "cannot_tell_adversarial"]
        assert adv["clears_both"] is False, field


def test_the_secondary_threshold_decides_nothing():
    for field in UNDER_TEST:
        assert _arm(field, "secondary")["clears_both"] is False, field


# ---------------------------------------------------------------------------
# the finding most likely to be forgotten
# ---------------------------------------------------------------------------

def test_the_lift_bar_is_unreachable_for_hands_by_any_detector():
    cell = _arm("hands_visible")
    assert cell["max_attainable_lift"] == 0.0882
    assert cell["max_attainable_lift"] < BAR_LIFT
    assert cell["lift_bar_attainable"] is False


def test_the_lift_bar_is_reachable_for_piano():
    cell = _arm("piano_or_keyboard_visible")
    assert cell["max_attainable_lift"] == 0.1487
    assert cell["lift_bar_attainable"] is True


def test_text_overlay_is_unmeasured_rather_than_failed():
    cell = _result()["fields"]["text_overlay_present"]
    assert cell["arms"] == {}
    assert cell["answers"] == {"yes": 107, "no": 4, "cannot_tell": 0}
    assert "no arm to score" in cell["unmeasured"]


# ---------------------------------------------------------------------------
# the frame set, which nearly invalidated the run
# ---------------------------------------------------------------------------

def test_the_arm_was_scored_on_the_frames_the_owner_judged():
    assert "FramePlan[:MAX_FRAMES]" in _result()["_frames"]


def test_the_frame_set_correction_is_recorded_as_material():
    effect = _result()["frame_set_effect"]
    assert effect["piano_or_keyboard_visible"][
        "publications_changing_side_of_threshold"] == 9
    assert effect["hands_visible"][
        "publications_changing_side_of_threshold"] == 8


def test_the_protocol_records_the_defect_before_the_result_exists():
    text = PROTOCOL.read_text(encoding="utf-8")
    assert "7.1 The frame set" in text
    assert "withdrawn" in text


# ---------------------------------------------------------------------------
# what must not happen next
# ---------------------------------------------------------------------------

def test_the_holdout_is_marked_spent():
    assert "7e-holdout-result.md" in _result()["_spent_by"]
    drawn = json.loads((FIXTURES / "holdout_prospective.json").read_text(
        encoding="utf-8"))
    assert drawn.get("_spent_by"), (
        "the draw must record what spent it, as the M3 fixtures do")


def test_the_cohort_limitation_is_recorded_on_the_result():
    assert "own account only" in _result()["_cohort"]


def test_neither_field_entered_analytical_use():
    for field in UNDER_TEST:
        name = f"vision_{field}"
        assert name not in vs.VISION_FEATURES
        assert name in vs.WITHHELD_VISION


def test_the_withheld_reason_records_the_owlv2_measurement():
    # The older figures are the VLM's; this holdout measured OWLv2.
    # Both are kept, and the reason says which is which.
    for field in UNDER_TEST:
        reason = vs.WITHHELD_VISION[f"vision_{field}"]
        assert "OWLv2" in reason, field
        assert "holdout" in reason.lower(), field


def test_the_report_matches_the_data():
    text = REPORT.read_text(encoding="utf-8")
    for fragment in ("0.926", "+0.060", "1.000", "−0.011", "0.988",
                     "+0.088", "+0.013", "+0.149", "9 of 111",
                     "8 of 111", "27 negatives", "15 negatives"):
        assert fragment in text, fragment


def test_the_report_does_not_claim_a_promotion():
    text = REPORT.read_text(encoding="utf-8").lower()
    assert "nothing is promoted" in text
    assert "not promoted" in text


# ---------------------------------------------------------------------------
# the published cells are recomputable from committed files alone
# ---------------------------------------------------------------------------

def test_the_published_cells_recompute_from_the_committed_evidence():
    """No server, no GPU, no manifest on a machine that got wiped.

    The arm scores, the owner's answers and the draw are all in the
    repository, so every number on the report page can be rebuilt
    here. If a later edit "tidies" a figure in the fixture, this
    fails.
    """
    from scripts.provision.score_holdout import score_field

    result = _result()
    arm = json.loads((FIXTURES / "holdout_arm_scores.json").read_text(
        encoding="utf-8"))["scores"]
    answers = json.loads((FIXTURES / "holdout_answers.json").read_text(
        encoding="utf-8"))["answers"]
    weight = {r["video_id"]: r["weight"] for r in result["sample"]}
    stratum = {r["video_id"]: r["stratum"] for r in result["sample"]}

    for field in UNDER_TEST:
        cut = result["fields"][field]["arms"]["primary"]["threshold"]
        rows = [{"y": answers[str(v)][field] == "yes",
                 "pred": arm[str(v)][field] >= cut,
                 "w": weight[v], "stratum": stratum[v]}
                for v in sorted(weight)
                if answers[str(v)][field] in ("yes", "no")]
        rebuilt = score_field(rows)
        published = _arm(field)
        for key in ("n", "tp", "fp", "tn", "fn", "specificity", "recall",
                    "base_rate", "accuracy", "lift",
                    "max_attainable_lift", "clears_both"):
            assert rebuilt[key] == published[key], f"{field}.{key}"


def test_the_committed_arm_is_the_six_frame_pass_not_the_strata_pass():
    arm = json.loads((FIXTURES / "holdout_arm_scores.json").read_text(
        encoding="utf-8"))
    assert "FramePlan[:MAX_FRAMES]" in arm["_what"]
    assert len(arm["scores"]) == 111
