"""The best vision result so far, and the reason it is not a promotion.

Claude Sonnet answered all 40 own-account publications with zero
errors on `piano_or_keyboard_visible`, `hands_visible` and
`text_overlay_present` - the questions that defeated Gemma, CLIP,
OWLv2 and Qwen. Written up in `docs/benchmarks/7d-claude-sonnet.md`.

A result that good is the moment to be most careful, and these tests
exist for the three specific ways it could be believed too hard:

1. **The labels were written by Claude.** A perfect score against an
   answer key written by another Claude model is exactly the shape a
   correlated-error artefact takes. The fixture has to keep saying so.
2. **The denominators are tiny.** Three negatives for hands, six for
   piano, one for text overlay. "No errors on three cases" is not a
   specificity of 1.00, and nothing may quietly treat it as one.
3. **Nothing may be promoted on it.** The corpus was spent choosing a
   prompt in M3, a model in Phase 1 and a provider here.
"""

from __future__ import annotations

import json
from pathlib import Path

from server.ai import vision_questions as vq
from server.features import visual_structure as vs

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "vision"
SONNET = FIXTURES / "claude_sonnet_comparison.json"

#: Below this, a proportion is a gesture rather than a measurement.
ENOUGH_NEGATIVES = 15


def _data() -> dict:
    return json.loads(SONNET.read_text(encoding="utf-8"))


def _field(name: str) -> dict:
    return _data()["combined_40"][name]


# ---------------------------------------------------------------------------
# the result itself
# ---------------------------------------------------------------------------

def test_the_full_forty_are_reported_not_the_thirty_three_that_parsed():
    """The first pass answered 33; seven truncated. Complex scenes make
    long descriptions and complex scenes are the hard cases, so 33/40
    would have been a score with the hard ones quietly removed."""
    d = _data()
    for name, cell in d["combined_40"].items():
        assert cell["n"] == 40, f"{name} scored on {cell['n']}, not 40"
    assert "truncated" in d["_two_passes"]


def test_it_makes_no_errors_on_the_three_fields_that_defeated_everything():
    for name in ("piano_or_keyboard_visible", "hands_visible",
                 "text_overlay_present"):
        cell = _field(name)
        assert cell["fp"] == 0 and cell["fn"] == 0, name


def test_it_beats_every_local_approach_on_the_piano_question():
    """The comparison that justifies paying for it at all. Every local
    approach measured sits at or below 0.50; this is the number to
    beat if a free model is ever to replace it."""
    local_best = 0.50          # OWLv2-base and Qwen, see 7a and 7c
    assert _field("piano_or_keyboard_visible")["specificity"] > local_best


def test_the_cost_is_settled_from_real_token_counts():
    d = _data()
    assert d["usd_per_publication"] > 0
    assert d["billed_usd_ledger"] > 0
    # The two passes bill less than their nominal sum, because the
    # re-run matched idempotency keys for calls already settled.
    assert d["billed_usd_ledger"] <= d["billed_usd_total"] + 1e-9


# ---------------------------------------------------------------------------
# the three ways to believe it too hard
# ---------------------------------------------------------------------------

def test_the_fixture_records_that_the_labels_are_not_independent():
    """The single most important sentence in the file. A model may
    write the answer key or sit the exam, not both."""
    corpus = _data()["_corpus"].lower()
    assert "not independent" in corpus
    assert "claude" in corpus


def test_the_corpus_is_recorded_as_spent():
    assert "spent" in _data()["_corpus"].lower()


def test_a_perfect_score_on_a_handful_of_negatives_is_labelled_as_such():
    """Piano, hands and text overlay all score 1.000 on 6, 3 and 1
    negatives. The fixture must carry the denominator next to the
    proportion, so nothing can quote the one without the other."""
    for name in ("piano_or_keyboard_visible", "hands_visible",
                 "text_overlay_present"):
        cell = _field(name)
        assert cell["specificity"] == 1.0
        assert cell["negatives_available"] < ENOUGH_NEGATIVES, (
            f"{name} now has enough negatives to mean something - "
            f"revisit this test deliberately")
        assert "negatives_available" in cell


def test_the_fields_with_real_denominators_are_good_not_perfect():
    """Sheet music and face have 28 and 24 negatives, and score 0.857
    and 0.833 - strong, and visibly not perfect. The contrast is what
    shows the perfect scores above are about sample size."""
    for name in ("sheet_music_visible", "face_visible"):
        cell = _field(name)
        assert cell["negatives_available"] >= ENOUGH_NEGATIVES
        assert 0.70 < cell["specificity"] < 1.0, name


def test_nothing_countable_was_promoted_on_this_result():
    """The guard. This is the strongest vision result the project has
    produced and it still may not support a claim: only the
    prospective holdout in 7b-holdout-protocol.md can do that.

    The invariant is **cannot be counted**, not a literal grade.
    These three moved to `indicative` on 2026-10-28, after the owner
    corrected the labels and the Haiku wide run measured against the
    corrected set - which earns them a place on screen as a reading
    and earns them nothing else. Asserting the string `unverified`
    would have forced a choice between hiding a usable description
    and promoting an unproven claim, and the string was never the
    thing worth protecting.
    """
    for short in ("piano_or_keyboard_visible", "hands_visible",
                  "sheet_music_visible"):
        assert f"vision_{short}" in vs.WITHHELD_VISION, short
        q = vq.by_name(short)
        assert q is not None
        assert q.reliability != "verified", short
        assert not vq.may_enter_statistics(short), (
            f"{short} became countable on a spent corpus whose labels "
            f"were written by the same model family that was measured")
