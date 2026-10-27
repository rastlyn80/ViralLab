"""The promotion gate: a vision field may only be used if it was measured.

M3's rule was written down before anything was scored - **specificity
>= 0.80 and lift >= +0.10 on the held-out set** - and the held-out set
is the 80 publications in the two label fixtures here.

**These tests recompute the verdict; they do not trust a number.** The
raw model output for both arms is committed beside the labels, so the
confusion matrix behind every promoted field is rebuilt here from
predictions and labels and checked against what
`visual_structure.VISION_FEATURES` actually contains. The arithmetic is
written out longhand rather than imported from
`scripts/provision/score_m3_arms.py` on purpose: a check that reuses
the implementation it is checking cannot catch that implementation
being wrong.

`HELD_OUT_CONTROL_ARM` below is kept as a cross-check of the published
figures, not as the source of truth. If the recomputation and the
constants ever disagree, this fails and the documents are wrong.

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
PRED_OWN = FIXTURES / "holdout_predictions_own.json"
PRED_COMPETITOR = FIXTURES / "holdout_predictions_competitor.json"

#: The arm the pipeline runs, and the arm the promotions were measured
#: under. They must be the same thing; a test below asserts it.
CONTROL_ARM = "original"
TREATMENT_ARM = "negative_example"

MIN_SPECIFICITY = 0.80
MIN_LIFT = 0.10

#: The six presence booleans the task schema requires and the labels
#: carry. `shot_distance` and `setting` are categorical and scored
#: differently, so they are not in here.
PRESENCE_FIELDS = (
    "performer_visible", "piano_or_keyboard_visible", "hands_visible",
    "face_visible", "sheet_music_visible", "text_overlay_present",
)

#: Published in docs/benchmarks/6c-vision-benchmark.md and quoted in the
#: registry. Cross-checked against the recomputation below, never used
#: in place of it.
HELD_OUT_CONTROL_ARM = {
    "vision_text_overlay_present": {"specificity": 0.917, "lift": 0.138},
    "vision_face_visible": {"specificity": 0.808, "lift": 0.175},
}


def _labels(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _predictions(arm: str) -> dict[int, dict]:
    """video_id -> the model's object, pooled over both halves."""
    out: dict[int, dict] = {}
    for path in (PRED_OWN, PRED_COMPETITOR):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for row in doc["results"]:
            if row["arm"] == arm and row["status"] == "ok":
                out[int(row["video_id"])] = row["data"]
    return out


def _truth() -> dict[int, dict]:
    out: dict[int, dict] = {}
    for path in (OWN, COMPETITOR):
        out.update({int(k): v for k, v in _labels(path)["labels"].items()})
    return out


def measure(field: str, arm: str) -> dict:
    """Recompute specificity and lift for one field from the fixtures.

    Longhand on purpose. `field` is the bare schema name, without the
    `vision_` prefix the feature registry uses.
    """
    preds, truth = _predictions(arm), _truth()
    tp = tn = fp = fn = 0
    for vid, want_row in truth.items():
        if field not in want_row or vid not in preds:
            continue
        got = preds[vid].get(field)
        if got is None or got == "unknown":
            continue
        got, want = bool(got), bool(want_row[field])
        if got and want:
            tp += 1
        elif not got and not want:
            tn += 1
        elif got and not want:
            fp += 1
        else:
            fn += 1
    n = tp + tn + fp + fn
    pos = tp + fn
    baseline = max(pos, n - pos) / n if n else 0.0
    accuracy = (tp + tn) / n if n else 0.0
    return {
        "n": n, "tp": tp, "tn": tn, "fp": fp, "fn": fn,
        "has_negatives": 0 < pos < n,
        "specificity": (tn / (tn + fp)) if (tn + fp) else None,
        "accuracy": accuracy,
        "baseline": baseline,
        "lift": accuracy - baseline,
    }


def clears(m: dict) -> bool:
    return bool(m["has_negatives"]
                and m["specificity"] is not None
                and m["specificity"] >= MIN_SPECIFICITY
                and m["lift"] >= MIN_LIFT)


# ---------------------------------------------------------------------------
# the gate, recomputed from the evidence on disk
# ---------------------------------------------------------------------------

def test_every_promoted_field_clears_the_bar_when_recomputed():
    """Not "is there a number recorded" but "does the evidence on disk
    still support the promotion"."""
    for name in vs.VISION_FEATURES:
        m = measure(name.removeprefix("vision_"), CONTROL_ARM)
        assert m["n"] == 80, (name, m["n"])
        assert clears(m), (
            f"{name} is promoted but recomputes to specificity "
            f"{m['specificity']:.3f} and lift {m['lift']:+.3f}")


def test_no_withheld_presence_field_would_have_cleared_the_bar():
    """The other direction, which is easy to forget.

    A withheld field that now clears the bar is either a stale decision
    or a bug in the measurement, and either way somebody should look.
    """
    surprises = [
        name for name in vs.WITHHELD_VISION
        if name.removeprefix("vision_") in PRESENCE_FIELDS
        and clears(measure(name.removeprefix("vision_"), CONTROL_ARM))
    ]
    assert not surprises, (
        f"withheld but clears the bar under the production prompt: "
        f"{surprises}")


def test_the_published_figures_match_the_recomputation():
    """`HELD_OUT_CONTROL_ARM` is quoted in the registry and in
    docs/benchmarks/6c-vision-benchmark.md. If it drifts from the
    fixtures the documents are lying, and this is what notices."""
    for name, published in HELD_OUT_CONTROL_ARM.items():
        m = measure(name.removeprefix("vision_"), CONTROL_ARM)
        assert m["specificity"] == pytest.approx(
            published["specificity"], abs=0.001), name
        assert m["lift"] == pytest.approx(published["lift"], abs=0.001), name


def test_the_treatment_arm_result_is_reproducible_too():
    """The reason sheet music is NOT promoted is itself a measurement,
    and it has to survive in the repository or the decision cannot be
    revisited later."""
    control = measure("sheet_music_visible", CONTROL_ARM)
    treat = measure("sheet_music_visible", TREATMENT_ARM)
    assert control["specificity"] == pytest.approx(0.193, abs=0.001)
    assert treat["specificity"] == pytest.approx(0.807, abs=0.001)
    assert clears(treat) and not clears(control)
    # ...and the trade that stopped it being adopted: the treatment arm
    # loses both of the fields that are currently promoted.
    assert not clears(measure("face_visible", TREATMENT_ARM))
    assert not clears(measure("text_overlay_present", TREATMENT_ARM))


def test_the_predictions_cover_both_arms_and_every_publication():
    """A fixture quietly missing rows would leave every figure above
    looking fine and meaning something else."""
    truth = set(_truth())
    assert len(truth) == 80
    for arm in (CONTROL_ARM, TREATMENT_ARM):
        preds = _predictions(arm)
        assert set(preds) == truth, (
            f"{arm}: {len(truth - set(preds))} publication(s) missing")


def test_the_prediction_fixtures_carry_no_media():
    """ADR 0009. The competitor frames these came from were deleted;
    the fixtures must hold model answers and nothing else, or the
    retention guarantee has a hole in the test directory."""
    for path in (PRED_OWN, PRED_COMPETITOR):
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert "_contains_no_media" in doc
        blob = json.dumps(doc)
        assert "jpeg_b64" not in blob
        assert "frames" not in {k for row in doc["results"]
                                for k in row if k == "frames"}


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
    assert vision_task.PROMPT_VARIANTS[CONTROL_ARM][0] == "PROMPT"


# ---------------------------------------------------------------------------
# the held-out set is now spent, and must say so
# ---------------------------------------------------------------------------

def test_the_held_out_set_records_that_it_has_been_used_for_a_decision():
    """These 80 publications chose a prompt and promoted a field. That
    is exactly what the 24 tuning labels did before them, and it spends
    them the same way: a set used to select can no longer measure
    generalisation of what it selected. M4 needs a fresh prospective
    holdout, and the fixtures have to carry that warning or the next
    person will reuse them without noticing.
    """
    for path in (OWN, COMPETITOR):
        doc = _labels(path)
        assert "_spent_by" in doc, f"{path.name} does not record its use"
        spent = doc["_spent_by"]
        assert "M3" in spent
        assert "prospective" in spent.lower()


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
