"""Phase 1 of the visual-intelligence repair: the comparison, and its limits.

Four approaches were benchmarked against the M3 baseline on identical
frames - Gemma 3 4B, the deterministic hook detectors, OpenCLIP
ViT-B/32, and OWLv2 at two sizes. The metrics are committed in
`detector_comparison.json` and written up in
`docs/benchmarks/7-detector-comparison.md`.

**The job of this file is to stop the report and the data drifting
apart, and to stop the report being mistaken for a promotion.**

The second is the one that matters. OWLv2-base scores 0.90 specificity
with +0.20 lift on sheet music, which is the best result in the project
so far and is *still not grounds to promote the field*: the 80
publications behind it were spent choosing a prompt in M3 and spent
again choosing a model here. A future session reading only the headline
would have every reason to switch `sheet_music_visible` on. The test at
the bottom of this file is what stops it.

The structural finding is tested too, because it is easy to forget and
it governs the design of the next holdout: three of the five fields have
so few negatives that specificity cannot be measured on them at all.
"""

from __future__ import annotations

import json
from pathlib import Path

from server.features import visual_structure as vs

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "vision"
COMPARISON = FIXTURES / "detector_comparison.json"

MIN_SPECIFICITY = 0.80
MIN_LIFT = 0.10

DIRECTIONS = ("tuned_on_own_tested_on_competitor",
              "tuned_on_competitor_tested_on_own")

#: Below this many negatives, specificity is not a measurement. With
#: 3 negatives the attainable values are 0, 0.33, 0.67 and 1.00, so a
#: bar of 0.80 can only be met by a perfect score and one borderline
#: video decides it.
ENOUGH_NEGATIVES = 15


def _comparison() -> dict:
    return json.loads(COMPARISON.read_text(encoding="utf-8"))


def _cell(run: dict, field: str, direction: str) -> dict | None:
    return run.get("fields", {}).get(field, {}).get(direction)


def _negatives(cell: dict) -> int:
    return cell["tn"] + cell["fp"]


# ---------------------------------------------------------------------------
# the data behind the report
# ---------------------------------------------------------------------------

def test_every_approach_in_the_report_has_its_numbers_committed():
    d = _comparison()
    assert set(d["approaches"]) == {
        "gemma", "deterministic", "clip_vit_b32", "owlv2_base",
        "owlv2_large", "owlv2_base_discriminative_FAILED"}


def test_the_corpus_is_recorded_as_spent():
    """The one sentence that keeps these numbers out of a promotion."""
    assert "spent" in _comparison()["_corpus"].lower()


def test_the_bars_are_the_pre_registered_ones():
    bars = _comparison()["_bars"]
    assert bars["specificity"] == MIN_SPECIFICITY
    assert bars["lift"] == MIN_LIFT


def test_the_harness_reproduces_gemmas_published_figures():
    """Gemma's numbers were published in M3 from a different script. If
    this harness disagrees with them, the harness is wrong, and every
    other row in the comparison is worthless."""
    g = _comparison()["approaches"]["gemma"]
    face_a = _cell(g, "face_visible", DIRECTIONS[0])
    face_b = _cell(g, "face_visible", DIRECTIONS[1])
    assert round(face_a["specificity"], 3) == 0.857
    assert round(face_b["specificity"], 3) == 0.750

    text_a = _cell(g, "text_overlay_present", DIRECTIONS[0])
    text_b = _cell(g, "text_overlay_present", DIRECTIONS[1])
    assert round(text_a["specificity"], 3) == 0.909
    assert round(text_b["specificity"], 3) == 1.000

    # Only one direction is scored: in the other, Gemma answers yes to
    # every single video, so there is no threshold to tune and the
    # harness declines to invent one. That is itself the finding.
    piano = [_cell(g, "piano_or_keyboard_visible", d) for d in DIRECTIONS]
    assert any(c is None for c in piano), (
        "Gemma used to be unscoreable on piano in one direction because "
        "it never said no. If it now varies, M3's finding has changed.")
    for cell in filter(None, piano):
        assert cell["specificity"] == 0.0, (
            "Gemma says yes to a piano regardless of the image; that is "
            "the M3 finding this whole phase exists to repair")


# ---------------------------------------------------------------------------
# the structural finding: three fields cannot be judged on this corpus
# ---------------------------------------------------------------------------

def test_piano_and_hands_have_too_few_negatives_to_judge():
    """Not 'the detector failed'. 'Nobody has measured it.'

    A concert pianist's videos contain pianos and hands, so the M3
    sample left 4 and 6 negatives for piano and 7 and 3 for hands.
    Every piano and hands figure in the comparison - including the ones
    that look like improvements - is unmeasured rather than failed.
    """
    owl = _comparison()["approaches"]["owlv2_base"]
    for field in ("piano_or_keyboard_visible", "hands_visible"):
        worst = min(_negatives(_cell(owl, field, d)) for d in DIRECTIONS)
        assert worst < ENOUGH_NEGATIVES, (
            f"{field} now has {worst} negatives in its thinnest "
            f"direction. If a holdout with deliberate negatives has "
            f"been built, this test has served its purpose and the "
            f"field can finally be judged - update it deliberately.")


def test_sheet_music_and_face_do_have_enough_negatives():
    """The contrast that makes the previous test a finding rather than
    an excuse. The same corpus supports a real measurement on two
    fields and not on the other three."""
    owl = _comparison()["approaches"]["owlv2_base"]
    for field in ("sheet_music_visible", "face_visible"):
        worst = min(_negatives(_cell(owl, field, d)) for d in DIRECTIONS)
        assert worst >= ENOUGH_NEGATIVES, field


def test_text_overlay_rests_on_a_single_negative_in_the_own_cohort():
    g = _comparison()["approaches"]["gemma"]
    cell = _cell(g, "text_overlay_present", DIRECTIONS[1])
    assert _negatives(cell) == 1, (
        "text_overlay_present scores 1.00 specificity on the own cohort "
        "because there is exactly one video there without on-screen "
        "text. That is a coin, not a measurement.")


# ---------------------------------------------------------------------------
# what the comparison actually supports
# ---------------------------------------------------------------------------

def test_owlv2_base_beats_the_baseline_where_the_question_is_answerable():
    """Sheet music is the field with a sound denominator, and it is the
    field where open-vocabulary detection is categorically better than
    the captioning VLM: Gemma has negative lift in both directions."""
    a = _comparison()["approaches"]
    for direction in DIRECTIONS:
        gemma = _cell(a["gemma"], "sheet_music_visible", direction)
        owl = _cell(a["owlv2_base"], "sheet_music_visible", direction)
        assert gemma["lift"] < 0
        assert owl["lift"] >= MIN_LIFT
        assert owl["specificity"] > gemma["specificity"] + 0.4


def test_the_large_model_does_not_earn_its_cost():
    """5.1x the inference time for a *lower* lift on the one field that
    can be judged. Recorded so the choice is not revisited by vibe."""
    a = _comparison()["approaches"]
    base, large = a["owlv2_base"], a["owlv2_large"]
    assert large["meta"]["seconds"] > 4 * base["meta"]["seconds"]
    assert large["meta"]["vram_mib"] > base["meta"]["vram_mib"]
    small = _cell(base, "sheet_music_visible", DIRECTIONS[0])
    big = _cell(large, "sheet_music_visible", DIRECTIONS[0])
    assert big["lift"] <= small["lift"]


def test_clip_cannot_answer_questions_about_parts_of_a_scene():
    """0.00 specificity on both piano and hands, both directions but
    one. Whole-image similarity cannot say whether the keys are in
    shot, and the brief's warning not to read these scores as
    calibrated probabilities is upheld rather than assumed."""
    clip = _comparison()["approaches"]["clip_vit_b32"]
    cells = [_cell(clip, f, d) for d in DIRECTIONS
             for f in ("piano_or_keyboard_visible", "hands_visible")]
    assert sum(c["specificity"] == 0.0 for c in cells) >= 3
    assert all(c["lift"] < MIN_LIFT for c in cells)


def test_the_discriminative_query_experiment_is_recorded_as_a_failure():
    """Subtracting 'a grand piano with no keys visible' from 'a piano
    keyboard' was the remedy the failure analysis suggested. OWLv2
    scores both phrasings on the same box, so it removed signal from
    true positives as fast as from false ones. Kept because a negative
    result nobody can find gets retried."""
    a = _comparison()["approaches"]
    plain = a["owlv2_base"]
    disc = a["owlv2_base_discriminative_FAILED"]
    worse = 0
    for direction in DIRECTIONS:
        p = _cell(plain, "piano_or_keyboard_visible", direction)
        d = _cell(disc, "piano_or_keyboard_visible", direction)
        assert d["lift"] <= p["lift"]
        worse += d["specificity"] < p["specificity"]
    assert worse, "the experiment is filed as a failure; it must look like one"


def test_no_approach_clears_both_bars_in_both_directions():
    """The headline, asserted rather than written down. If some future
    detector does clear both bars both ways, this fails loudly and the
    report needs rewriting - which is the correct outcome."""
    for name, run in _comparison()["approaches"].items():
        for field in run.get("fields", {}):
            cells = [_cell(run, field, d) for d in DIRECTIONS]
            if any(c is None for c in cells):
                continue
            clears = all(c["specificity"] >= MIN_SPECIFICITY
                         and c["lift"] >= MIN_LIFT for c in cells)
            assert not clears, (
                f"{name}/{field} clears both bars in both directions on "
                f"the SPENT dev set. That is not a promotion - it is a "
                f"candidate for the prospective holdout.")


# ---------------------------------------------------------------------------
# the guard that matters
# ---------------------------------------------------------------------------

def test_phase_1_promoted_nothing():
    """A model-selection decision on a spent dev set is legitimate. A
    promotion on one is not, however good the number looks.

    `sheet_music_visible` at 0.90/+0.20 is the strongest result the
    project has produced and it stays withheld until a holdout that has
    never been tuned on says the same thing.
    """
    allowed = set(vs.VISION_FEATURES)
    for short in ("piano_or_keyboard_visible", "hands_visible",
                  "sheet_music_visible"):
        field = f"vision_{short}"
        assert field in vs.WITHHELD_VISION, (
            f"{field} was promoted. Phase 1 measured it on data that had "
            f"already been spent twice; only the prospective holdout in "
            f"Phase 4 can promote anything.")
        assert field not in allowed
