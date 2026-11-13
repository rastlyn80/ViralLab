"""The check that would have saved holdout 1, enforced before holdout 2.

Holdout 1 spent 333 of the owner's answers on three questions, and
two of them could not have passed whatever the detector did: a
flawless answerer scores +0.088 on hands and +0.013 on text overlay
against a +0.10 lift bar. Nobody noticed until the result was in.

The arithmetic that catches it is one line, and these tests make it
a precondition of drawing a holdout rather than a lesson somebody has
to remember. See `docs/benchmarks/7f-holdout-2-protocol.md` section 2.
"""

from __future__ import annotations

from pathlib import Path

from scripts.provision.score_holdout import (BAR_LIFT, M3_BASE_RATES,
                                             attainable_lift,
                                             lift_bar_is_attainable)

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_2 = ROOT / "docs" / "benchmarks" / "7f-holdout-2-protocol.md"

#: Drawn in holdout 2. Nothing else may be added without passing the
#: check below on both cohorts.
ROUND_2_FIELDS = ("face_visible", "sheet_music_visible")

#: Settled as questions about his account, and not asked again.
RETIRED = ("piano_or_keyboard_visible", "hands_visible",
           "text_overlay_present")


def test_the_arithmetic_is_what_it_claims_to_be():
    # A question answered the same way every time leaves nothing to win.
    assert attainable_lift(0.5) == 0.5
    assert attainable_lift(1.0) == 0.0
    assert attainable_lift(0.912) < 0.09
    assert attainable_lift(0.3) == attainable_lift(0.7)


def test_every_field_drawn_in_holdout_2_can_actually_clear_the_bar():
    for field in ROUND_2_FIELDS:
        for cohort, rate in M3_BASE_RATES[field].items():
            assert lift_bar_is_attainable(rate), (
                f"{field} on the {cohort} cohort: a flawless answerer "
                f"scores {attainable_lift(rate):+.3f} against a "
                f"{BAR_LIFT:+.2f} bar")


def test_the_retired_questions_would_have_been_refused():
    # Hands and text overlay fail on the owner's own cohort, which is
    # the cohort holdout 1 drew from.
    for field in ("hands_visible", "text_overlay_present"):
        assert not lift_bar_is_attainable(M3_BASE_RATES[field]["own"]), field


def test_piano_was_reachable_and_still_failed():
    # The one retired question whose bar was reachable. It is retired
    # for a different reason - it was measured and it lost.
    assert lift_bar_is_attainable(M3_BASE_RATES[
        "piano_or_keyboard_visible"]["own"])


def test_holdout_2_asks_only_the_two_questions():
    text = PROTOCOL_2.read_text(encoding="utf-8")
    for field in ROUND_2_FIELDS:
        assert f"`{field}`" in text
    assert "**not asked again**" in text


def test_holdout_2_requires_both_halves():
    flat = " ".join(PROTOCOL_2.read_text(encoding="utf-8").split())
    assert "separately, never pooled" in flat
    assert "80 of his publications and 80 of other creators'" in flat
    # Each half judged on its own, and a pass on one is not a pass.
    assert "clearing the own half alone" in flat
    assert "clearing the competitor half alone" in flat


def test_holdout_2_fixes_its_thresholds_before_drawing():
    text = PROTOCOL_2.read_text(encoding="utf-8")
    # Each half is decided by the threshold tuned on the other cohort.
    for value in ("0.0879", "0.1709", "0.3603", "0.3167"):
        assert value in text, value
    assert "in_sample_optimistic" in text


def test_holdout_2_inherits_the_frame_rule():
    text = PROTOCOL_2.read_text(encoding="utf-8")
    assert "FramePlan().timestamps(duration)[:MAX_FRAMES]" in text
