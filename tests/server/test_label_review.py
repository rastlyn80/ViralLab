"""Letting the owner correct Claude, without letting the holdout be edited.

This screen exists because every vision benchmark in the project was
scored against labels Claude wrote, and the Claude Sonnet run of
2026-10-27 made that the binding problem: 40 of 40 against an answer
key written by another Claude model measures agreement as much as
correctness.

A review screen that can change labels is one keystroke away from a
screen that can change the answer key, so the guards here are about
what it must refuse:

1. **Only the flagged-ambiguous set.** Accepting any (video, field)
   pair would turn this into an editor for the holdout, which is a
   different and much larger thing than confirming twenty-one doubts.
2. **The model's label survives the verdict.** How often the labelling
   needed correcting is the measurement this whole exercise is for;
   overwriting it would destroy the only record of it.
3. **The reviewer is asked what they see, never whether they agree.**
   The first version hid the model's label and then asked for a
   verdict on it, which is unanswerable; agreement is now computed.
4. **Competitor media stays deleted.** ADR 0009 deleted it after
   measurement and a review screen must not become the path that
   quietly re-acquires it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.api import labels_api as lab

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "vision"


# ---------------------------------------------------------------------------
# the queue is the fixtures, not a hand-maintained list
# ---------------------------------------------------------------------------

def test_the_queue_comes_from_the_committed_fixtures():
    """If someone flags another label ambiguous, it appears here with
    no code change. A hardcoded list would drift the moment the
    labelling did."""
    items = lab._uncertain()
    assert items
    from_fixtures = 0
    for _cohort, name in lab.SOURCES:
        d = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        from_fixtures += len(d["_uncertain"])
    assert len(items) == from_fixtures


def test_every_queued_item_carries_what_the_model_said_or_why_it_did_not():
    """Without one of the two, a verdict records a human opinion with
    nothing to compare it against.

    A missing label is not always a gap. Publication 403 is a public
    piano in a railway concourse and the `setting` vocabulary has no
    value for it, so the label was deliberately OMITTED rather than
    forced into the nearest wrong box - and that omission is itself
    what the reviewer is being asked about.
    """
    for it in lab._uncertain():
        assert it["cohort"] in ("own", "competitor")
        assert it["why_uncertain"]
        if it["model_label"] is None:
            assert it["model_omitted"] is True
            assert "omitted" in it["why_uncertain"].lower()
        else:
            assert it["model_omitted"] is False


def test_a_deliberate_omission_is_asked_as_a_different_question():
    """"Was it right to leave this blank?" is not "is this label
    correct", and showing the reviewer the word `null` would be the
    interface admitting it had not thought about the case."""
    omitted = [i for i in lab._uncertain() if i["model_omitted"]]
    assert omitted, (
        "this test guards a real case - publication 403's `setting`. "
        "If the fixture no longer omits anything, retire it on purpose.")


def test_both_cohorts_are_represented():
    cohorts = {i["cohort"] for i in lab._uncertain()}
    assert cohorts == {"own", "competitor"}


def test_every_question_has_a_definition_a_person_can_apply():
    """A reviewer not shown the definition will re-create the
    ambiguity the definition exists to settle."""
    _rule, defs = lab._rulebook()
    for it in lab._uncertain():
        assert it["field"] in lab.QUESTIONS, it["field"]
        assert lab.QUESTIONS[it["field"]].endswith("?")
        assert len(defs.get(it["field"], "")) > 30, it["field"]


def test_the_definitions_are_read_from_the_fixture_not_restated():
    """A second copy of the rule drifts from the one the labels were
    made under, and then the reviewer is held to a standard the data
    was never built with."""
    _rule, defs = lab._rulebook()
    raw = json.loads(
        (FIXTURES / "holdout_labels_own.json").read_text(encoding="utf-8"))
    for k, v in raw["_definitions"].items():
        assert defs[k] == v, (
            f"{k} differs from the fixture - the reviewer would be held "
            f"to a rule the data was not built with")
    # The fixture defines neither `setting` nor `shot_distance`, which
    # is how 403 came to be blank. Those are supplied, and the
    # fixture's own wording still wins wherever it exists.
    assert defs["setting"] == lab.SUPPLIED_DEFINITIONS["setting"]
    assert "_definitions" in raw and "setting" not in raw["_definitions"]


def test_the_scope_rule_is_served_because_its_absence_caused_the_rework():
    """"A field is true if the thing is visible in ANY of the four
    frames." The first review page never showed it, and publication
    91 - "we can see the keys only in the last frame" - was filed as
    agreeing that no keys were visible. Under the stated rule that
    publication is TRUE."""
    rule, _defs = lab._rulebook()
    assert "ANY" in rule
    assert "frames" in rule


def test_the_piano_definition_states_the_distinction_that_caused_trouble():
    _rule, defs = lab._rulebook()
    d = defs["piano_or_keyboard_visible"].lower()
    assert "keys" in d
    assert "does not count" in d


# ---------------------------------------------------------------------------
# what a verdict may be
# ---------------------------------------------------------------------------

def test_the_reviewer_is_asked_what_they_see_not_whether_they_agree():
    """The correction that defines this version.

    The first attempt hid the model's label so it could not anchor
    the judgement, and then asked the reviewer to confirm or overturn
    it - a question nobody can answer about something they have not
    been shown. The owner's first pass proved it: four of eight
    "overturned" answers gave exactly the value the model had given,
    and three "confirmed" ones carried notes describing the opposite
    of what was confirmed.
    """
    a = lab.Answer(answer="no")
    assert a.answer == "no"
    assert not hasattr(a, "verdict")


@pytest.mark.parametrize("model,answer,expected", [
    ("false", "no", "confirmed"),
    ("false", "yes", "overturned"),
    ("true", "no", "overturned"),
    ("true", "yes", "confirmed"),
    ("true", "cannot_tell", "cannot_tell"),
    # A deliberately omitted label - publication 403's railway
    # concourse - is agreed with by saying you cannot tell either,
    # and overturned by supplying a value.
    (None, "cannot_tell", "confirmed"),
    (None, "public_space", "overturned"),
])
def test_agreement_is_computed_not_asserted(model, answer, expected):
    assert lab.derive_verdict(model, answer) == expected


@pytest.mark.parametrize("raw,norm", [
    ("Yes", "yes"), ("NO", "no"), ("true", "yes"), ("False", "no"),
    (" y ", "yes"), ("concert_hall", "concert_hall"),
])
def test_answers_are_normalised_before_comparison(raw, norm):
    """The owner typed "No sheet music at all" into a free-text box in
    the first pass. Case and padding must never decide an agreement."""
    assert lab._normalise(raw) == norm


@pytest.mark.parametrize("verdict", ["confirmed", "overturned",
                                     "cannot_tell"])
def test_every_derived_verdict_satisfies_the_database_constraint(verdict):
    """A derived value that the column refuses would fail at commit
    with a 500 after the reviewer had already clicked."""
    from server.db.models import LabelReview
    ck = next(c for c in LabelReview.__table__.constraints
              if getattr(c, "name", "") == "ck_label_reviews_verdict")
    assert verdict in str(ck.sqltext)


def test_a_yes_no_question_refuses_an_enum_value():
    import inspect
    src = inspect.getsource(lab.record_answer)
    assert "yes/no question" in src


def test_the_setting_vocabulary_was_widened_by_the_owners_complaint():
    """Publication 403 is a public piano in a railway concourse and
    the owner wrote "I don't like that the enum range is so
    restricted". The review screen must not force the same bad choice
    that produced the omission."""
    assert "public_space" in lab._SETTING_VALUES
    assert len(lab._SETTING_VALUES) > 6


def test_the_review_source_is_recorded_and_constrained():
    """Own media is judged from the frames the model saw; competitor
    media is gone and is judged from the live post. Those are
    different evidence and the row has to say which."""
    from server.db.models import LabelReview
    ck = next(c for c in LabelReview.__table__.constraints
              if getattr(c, "name", "") == "ck_label_reviews_source")
    assert "frames" in str(ck.sqltext)
    assert "live_post" in str(ck.sqltext)


def test_one_row_per_question_so_answering_twice_updates():
    from server.db.models import LabelReview
    idx = next(i for i in LabelReview.__table__.indexes
               if i.name == "uq_label_reviews_question")
    assert idx.unique
    assert {c.name for c in idx.columns} == {"video_id", "field"}


def test_the_model_label_column_exists_beside_the_owners():
    """Kept whatever the owner decides. The error rate of the
    labelling process is the thing being measured."""
    from server.db.models import LabelReview
    cols = {c.name for c in LabelReview.__table__.columns}
    assert {"model_label", "owner_label", "verdict"} <= cols


# ---------------------------------------------------------------------------
# what the endpoint must refuse
# ---------------------------------------------------------------------------

def test_only_flagged_labels_are_writable():
    """The guard that stops this becoming an editor for the holdout."""
    import inspect
    src = inspect.getsource(lab.record_answer)
    assert "not one of the" in src and "404" in src
    known = {(i["video_id"], i["field"]) for i in lab._uncertain()}
    assert (999999, "face_visible") not in known


def test_an_empty_or_nonsense_answer_is_refused():
    """The old failure mode - "the model was wrong" with no
    replacement - cannot occur now, because the only thing asked for
    IS the replacement. What remains is rejecting an answer the
    question does not accept."""
    import inspect
    src = inspect.getsource(lab.record_answer)
    assert "an answer is required" in src
    assert "400" in src


def test_frames_are_served_for_own_publications_only():
    """ADR 0009 deleted the competitor media after measurement. A
    review screen must not become the path that re-acquires it, and
    the check is on `is_own_account` rather than on a flag somebody
    could set."""
    import inspect
    src = inspect.getsource(lab.review_frames)
    assert "is_own_account" in src
    assert "403" in src
    assert "not retained" in src


def test_the_frames_endpoint_is_limited_to_the_flagged_set():
    import inspect
    src = inspect.getsource(lab.review_frames)
    assert "_uncertain()" in src


def test_writing_needs_the_csrf_guarded_dependency():
    """A GET may be read with a session; a PUT needs the header too."""
    import inspect
    src = inspect.getsource(lab)
    assert "require_ui_write" in src
    put = inspect.getsource(lab.record_answer)
    assert "video_id" in put


def test_the_summary_says_what_it_does_not_establish():
    """A 3-of-21 overturn rate on the hardest cases does not
    generalise to the labels nobody flagged, and it does not retract
    any published benchmark."""
    import inspect
    src = inspect.getsource(lab.summary)
    assert "does not generalise" in src or "not generalise" in src
    assert "not a correction" in src


# ---------------------------------------------------------------------------
# the owner's verdicts, and what may be done with them
# ---------------------------------------------------------------------------

def test_the_published_answer_key_was_not_rewritten():
    """Every benchmark already reported has to stay reproducible from
    `labels`. A fixture that silently corrected itself would make its
    own history unreadable, and `test_vision_promotion.py` recomputes
    the published confusion matrices from exactly this block."""
    for _cohort, name in lab.SOURCES:
        d = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        review = d.get("_owner_review")
        assert review, f"{name} carries no owner review"
        for r in review["verdicts"]:
            if r["verdict"] != "overturned":
                continue
            still = d["labels"].get(str(r["video_id"]), {}).get(r["field"])
            # `was` is what the published key says, and it still says it.
            if r["was"] is not None:
                assert str(still).lower() == r["was"], (
                    f"{name}: {r['video_id']} {r['field']} was rewritten "
                    f"in `labels`; corrections must stay additive")


def test_corrections_are_applied_by_the_accessor_not_the_file():
    corrected = lab.corrected_labels("own")
    raw = json.loads((FIXTURES / "holdout_labels_own.json")
                     .read_text(encoding="utf-8"))["labels"]
    changed = [(v, f) for v, fields in corrected.items()
               for f in set(fields) | set(raw.get(str(v), {}))
               if raw.get(str(v), {}).get(f) != fields.get(f)]
    assert changed, "the owner corrected something; it must show up here"


def test_cannot_tell_removes_the_label_rather_than_keeping_the_guess():
    """The owner looked at the same frames and could not decide. That
    is positive evidence the question has no answer here, and scoring
    a model against a coin-flip label rewards whichever way it fell."""
    corrected = lab.corrected_labels("own")
    d = json.loads((FIXTURES / "holdout_labels_own.json")
                   .read_text(encoding="utf-8"))
    unsure = [r for r in d["_owner_review"]["verdicts"]
              if r["verdict"] == "cannot_tell"]
    assert unsure, "this guards a real case; retire it deliberately"
    for r in unsure:
        assert r["field"] not in corrected.get(int(r["video_id"]), {})


def test_an_overturned_yes_no_becomes_a_boolean_not_the_string_yes():
    """`owner_said` is stored as text. A label of the string "yes"
    would compare unequal to a model's `True` and silently score every
    corrected publication wrong."""
    for cohort in ("own", "competitor"):
        for fields in lab.corrected_labels(cohort).values():
            for f, v in fields.items():
                if f.endswith("_visible") or f.endswith("_present"):
                    assert isinstance(v, bool), (f, v)


def test_the_review_records_what_it_does_not_establish():
    """Ten of twenty-one is the error rate on the hardest 4% of label
    decisions, not on the set. Stating it without that caveat would
    be the most misreadable number in the project."""
    d = json.loads((FIXTURES / "holdout_labels_own.json")
                   .read_text(encoding="utf-8"))
    found = d["_owner_review"]["_what_it_found"].lower()
    assert "not generalise" in found or "do not generalise" in found
    assert "hardest" in found
