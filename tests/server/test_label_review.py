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
3. **Overturning says what is right.** "The model was wrong" without a
   replacement records a problem and no answer.
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


def test_every_question_has_a_rule_a_person_can_apply():
    """The reason two models and the labeller disagreed about the piano
    is that "a piano is present" is not "the keys are visible". A
    reviewer who is not shown the rule will re-create the ambiguity."""
    for it in lab._uncertain():
        q = lab.QUESTIONS.get(it["field"])
        assert q is not None, f"no question text for {it['field']}"
        assert q["ask"].endswith("?")
        assert len(q["rule"]) > 30


def test_the_piano_rule_states_the_distinction_that_caused_the_trouble():
    rule = lab.QUESTIONS["piano_or_keyboard_visible"]["rule"].lower()
    assert "keys" in rule
    assert "not the question" in rule or "being there" in rule


# ---------------------------------------------------------------------------
# what a verdict may be
# ---------------------------------------------------------------------------

def test_cannot_tell_is_a_real_answer():
    """"A human could not tell either" says the question is ambiguous
    rather than that the model was wrong. Forcing a binary would
    manufacture certainty that does not exist."""
    v = lab.Verdict(verdict="cannot_tell")
    assert v.verdict == "cannot_tell"


def test_an_invented_verdict_is_refused():
    import pydantic
    with pytest.raises(pydantic.ValidationError):
        lab.Verdict(verdict="probably")


@pytest.mark.parametrize("verdict", ["confirmed", "overturned",
                                     "cannot_tell"])
def test_the_three_verdicts_match_the_database_constraint(verdict):
    """The check constraint and the request model have to agree, or a
    valid request fails at commit with a 500."""
    from server.db.models import LabelReview
    lab.Verdict(verdict=verdict, owner_label="no")
    ck = next(c for c in LabelReview.__table__.constraints
              if getattr(c, "name", "") == "ck_label_reviews_verdict")
    assert verdict in str(ck.sqltext)


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
    src = inspect.getsource(lab.record_verdict)
    assert "not one of the" in src and "404" in src
    known = {(i["video_id"], i["field"]) for i in lab._uncertain()}
    assert (999999, "face_visible") not in known


def test_overturning_without_a_replacement_is_refused():
    import inspect
    src = inspect.getsource(lab.record_verdict)
    assert "owner_label" in src
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
    put = inspect.getsource(lab.record_verdict)
    assert "video_id" in put


def test_the_summary_says_what_it_does_not_establish():
    """A 3-of-21 overturn rate on the hardest cases does not
    generalise to the labels nobody flagged, and it does not retract
    any published benchmark."""
    import inspect
    src = inspect.getsource(lab.summary)
    assert "does not generalise" in src or "not generalise" in src
    assert "not a correction" in src
