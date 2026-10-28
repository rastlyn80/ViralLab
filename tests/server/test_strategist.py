"""M8: a chat that cannot invent a number, tested where it is enforced.

BLUEPRINT acceptance is ten scripted questions answered with correct
citations and three trap questions refused. The trap half is tested
here directly, because those refusals are decided in code before any
model is called and so can be asserted without a network or a bill.

The ten-question half is tested as the thing that makes citation
possible rather than by scripting a model's prose: every tool returns
real data with ids and grades, and `unverified_numbers` catches any
figure in an answer that no tool produced. A test that asserted
particular sentences would be testing the model, which changes, and
not the guarantee, which must not.

The guarantees, in the order they matter:

1. **A number not in a tool result is caught.** This is the one that
   does not depend on the model cooperating, and it is the reason the
   whole design is allowed to use a language model at all.
2. **The refusal rules are enforced after generation.** A prompt is
   not a security boundary; `review()` reads the finished text.
3. **A withheld measurement is never served.** Not "the model is
   asked not to use it" - `publication` does not return it, so there
   is nothing to decline.
"""

from __future__ import annotations

import re

import pytest

from server.ai import strategist as st
from server.features import visual_structure as vs


# ---------------------------------------------------------------------------
# the three traps, refused before a model is called
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("question", [
    "Will this video go viral?",
    "will my next reel go viral if I use more contrast",
    "can you guarantee more views",
    "what would make this blow up",
])
def test_trap_one_reach_is_never_promised(question):
    out = st.precheck(question)
    assert out is not None
    assert "can't" in out.lower() or "cannot" in out.lower()
    # The refusal must say what CAN be done, or it is just a wall.
    assert "experiment" in out.lower() or "differ" in out.lower()


@pytest.mark.parametrize("question", [
    "Why did video 42 succeed?",
    "why did that one do well",
    "why was my Chopin video successful",
])
def test_trap_two_no_explanation_of_a_single_video(question):
    out = st.precheck(question)
    assert out is not None
    low = out.lower()
    assert "causes anything" in low or "caused" in low
    # The specific reason, not a generic refusal: one video has no
    # counterfactual.
    assert "counterfactual" in low or "single video" in low


@pytest.mark.parametrize("question", [
    "Who is the person in video 7?",
    "identify the performer in this frame",
    "what is his name",
])
def test_trap_three_nobody_is_identified(question):
    out = st.precheck(question)
    assert out is not None
    assert "identify" in out.lower()


def test_a_withheld_measurement_is_refused_with_its_reason():
    """The blueprint's trap: asking about a field that was measured
    and failed. The refusal carries the measurement's own basis, so
    the owner learns why rather than being stonewalled."""
    out = st.withheld_question(
        "what is the shot distance on most of my videos?")
    assert out is not None
    assert "withheld" in out.lower()
    assert len(out) > 120, "a refusal with no reason is a wall"


def test_an_ordinary_question_is_not_refused():
    """A precheck that refuses everything would pass every test above
    and be useless."""
    for q in ("How many of my videos show sheet music?",
              "What is edge density?",
              "How do my openings differ from the cohort?",
              "Which of my videos have an audience in shot?"):
        assert st.precheck(q) is None, q
        assert st.withheld_question(q) is None, q


# ---------------------------------------------------------------------------
# 1. a number no tool returned is caught
# ---------------------------------------------------------------------------

def _call(result):
    return st.ToolCall(name="feature_lookup", arguments={}, result=result)


def test_a_figure_from_a_tool_passes():
    calls = [_call({"coverage": 483, "grade": "Associated"})]
    assert st.unverified_numbers(
        "You have 483 publications measured.", calls) == []


def test_a_figure_nobody_returned_is_flagged():
    calls = [_call({"coverage": 483})]
    out = st.unverified_numbers(
        "You have 612 publications measured.", calls)
    assert "612" in out


def test_a_rounded_restatement_is_accepted():
    """"about 33%" from a stored 0.333 is a reasonable thing to write
    and flagging it would train the reader to ignore the warnings."""
    calls = [_call({"share": 0.333})]
    assert st.unverified_numbers("About 33% of them.", calls) == []


def test_a_percentage_derived_from_a_proportion_is_accepted():
    calls = [_call({"rate": 0.547})]
    assert st.unverified_numbers("54.7% of your videos.", calls) == []


def test_years_and_small_counts_are_not_treated_as_claims():
    """Flagging "two reasons" or "since 2024" would bury the real
    warnings in noise."""
    calls = [_call({})]
    assert st.unverified_numbers(
        "There are two reasons, and this has held since 2024.",
        calls) == []


def test_numbers_nested_deep_in_a_tool_result_are_found():
    calls = [_call({"sections": [{"rows": [{"n": 229, "rho": -0.31}]}]})]
    assert st.unverified_numbers(
        "Across 229 videos the relationship was -0.31.", calls) == []


# ---------------------------------------------------------------------------
# 2. the rules are enforced on the finished text
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "More contrast causes fewer people to skip.",
    "The brighter opening drives retention.",
    "Fewer skips because of the busier background.",
    "This leads to a better hold.",
    "Busier openings result in fewer skips.",
    "More detail will increase your watch time.",
])
def test_causal_language_is_caught_whatever_the_prompt_said(text):
    kinds = {v.kind for v in st.review(text, [])}
    assert "causal_language" in kinds


@pytest.mark.parametrize("text", [
    "This one could go viral.",
    "That would guarantee more reach.",
    "Expect millions of views.",
])
def test_a_reach_promise_in_the_answer_is_caught(text):
    kinds = {v.kind for v in st.review(text, [])}
    assert "virality_promise" in kinds


def test_an_associational_sentence_passes():
    """The check has to leave the correct phrasing alone, or the
    assistant cannot say anything true."""
    ok = ("Videos with more background detail also tended to have "
          "fewer viewers skipping them. That is an association, "
          "graded Associated, and it is not evidence of cause.")
    assert st.review(ok, []) == []


def _unverified_withheld() -> str:
    """A field nobody may even see, as opposed to one that is merely
    barred from a statistic.

    `WITHHELD_VISION` holds both since 2026-10-28: piano keys, hands
    and sheet music are withheld from analysis but graded
    `indicative`, which means showable. Picking an arbitrary member
    of the set would now pick one of those.
    """
    from server.ai import vision_questions as vq
    for key in vs.WITHHELD_VISION:
        short = key[len("vision_"):] if key.startswith("vision_") else key
        q = vq.by_name(short)
        if q is not None and q.reliability == "unverified":
            return key
    pytest.skip("no unverified withheld field remains")


def test_mentioning_an_unverified_field_is_allowed_only_as_unavailable():
    field = _unverified_withheld()
    bad = f"Looking at {field}, most of your openings are medium."
    kinds = {v.kind for v in st.review(bad, [])}
    assert "withheld_measurement" in kinds

    good = (f"{field} is withheld - it was measured and failed - so I "
            f"cannot answer from it.")
    assert "withheld_measurement" not in {v.kind for v in st.review(good, [])}


def test_an_indicative_field_may_be_discussed_freely():
    """The over-blocking the first live run exposed. "Is sheet music
    visible in publication 287?" is a description question about one
    video, answerable from data that exists. Refusing it was the
    enforcement being wrong, not strict."""
    assert st.withheld_question(
        "Is sheet music visible in publication 287?") is None
    assert st.review(
        "Sheet music is visible in that one, as an overlay.", []) == []


def test_a_violation_carries_the_text_that_tripped_it():
    """So a logged refusal can be read later without re-running it."""
    v = st.review("More contrast causes fewer skips.", [])[0]
    assert v.excerpt
    assert "cause" in v.excerpt.lower()
    assert v.detail


# ---------------------------------------------------------------------------
# 3. the tool surface
# ---------------------------------------------------------------------------

def test_exactly_the_five_tools_the_blueprint_names():
    assert set(st.TOOLS) == {"creator_report", "compare", "publication",
                             "feature_lookup", "evidence_for"}


def test_every_tool_is_declared_to_the_model_with_a_schema():
    declared = {s["name"] for s in st.TOOL_SCHEMAS}
    assert declared == set(st.TOOLS)
    for s in st.TOOL_SCHEMAS:
        assert s["description"]
        assert s["input_schema"]["properties"]
        assert s["input_schema"].get("required")


def test_no_tool_takes_free_text_that_could_become_a_query():
    """The blueprint forbids the model generating SQL. The defence is
    that no tool accepts anything a query could hide in: every input
    is an integer id or a feature name matched against the
    catalogue."""
    for s in st.TOOL_SCHEMAS:
        for name, spec in s["input_schema"]["properties"].items():
            assert spec["type"] in ("integer", "string"), (s["name"], name)
            if spec["type"] == "string":
                assert name in ("name", "feature", "outcome"), (
                    f"{s['name']}.{name} is free text the model controls")


def test_the_system_prompt_states_every_rule_the_code_enforces():
    """Prompt and code must agree. A rule enforced but not stated
    produces baffling refusals; a rule stated but not enforced is
    decoration."""
    p = st.SYSTEM.lower()
    assert "cause" in p
    assert "viral" in p
    assert "withheld" in p or "unavailable" in p
    assert "identify a person" in p or "never identify" in p
    assert "every number" in p


def test_the_prompt_tells_the_model_the_numbers_are_checked():
    """A model that knows the check exists is likelier to comply, and
    telling it costs nothing."""
    assert "checked in code" in st.SYSTEM.lower()


def test_the_loop_is_bounded():
    """A chat window is the one surface that can spend in a loop
    without anyone deciding to."""
    assert 1 <= st.MAX_TOOL_ROUNDS <= 10


def test_a_refused_budget_stops_the_conversation(monkeypatch):
    """Not "continues unledgered". The reserve callback returning None
    must end the turn."""
    class FakeProvider:
        MODEL, URL, API_VERSION = "m", "u", "v"
        USD_PER_MTOK_IN = USD_PER_MTOK_OUT = 1.0
        name = "fake"

        def _request_with_retries(self, *a, **k):   # pragma: no cover
            raise AssertionError("must not be called when budget refuses")

    out = st.ask(None, "how many videos show sheet music?",
                 provider=FakeProvider(), reserve=lambda est, key: None)
    assert out.blocked
    assert "budget" in (out.refusal or "").lower()


def test_an_answer_is_blocked_rather_than_shown_when_it_breaks_a_rule():
    a = st.Answer(text="More contrast causes fewer skips.",
                  violations=[st.Violation("causal_language", "x")],
                  blocked=True, refusal="refused")
    assert a.as_dict()["text"] == "refused"
    assert a.as_dict()["blocked"] is True


def test_the_denial_exemption_has_a_known_hole_and_it_is_recorded():
    """A causal verb near a negation is exempted, because the correct
    answer in this project says "this is not evidence of cause"
    constantly and blocking those would leave the assistant unable to
    state its own limits.

    The cost is a sentence that negates one thing and asserts cause
    about another inside the same window. That is a real gap, it is
    narrower than the alternative, and it is written down here rather
    than discovered later.

    If this ever starts mattering, the fix is clause-level parsing,
    not a longer word list.
    """
    slips_through = ("It does not hurt engagement, and the busier "
                     "opening causes fewer skips.")
    assert st.review(slips_through, []) == [], (
        "this documents a KNOWN limitation. If it now fails, the "
        "check got better - delete the test and say so.")

    # The hole is narrow: with the negation further away it is caught.
    caught = ("It does not hurt engagement. " + "Separately, " * 12
              + "the busier opening causes fewer skips.")
    assert any(v.kind == "causal_language" for v in st.review(caught, []))


def test_tool_output_survives_a_jsonb_column():
    """The cohort question died in the first live run with "Object of
    type datetime is not JSON serializable" - after the model had
    been called and the turn billed. Tool output goes into a JSONB
    column and into a JSON request body, so it is coerced once at the
    boundary rather than at each."""
    import json
    from datetime import datetime, timezone
    from decimal import Decimal

    raw = {"when": datetime(2026, 1, 2, tzinfo=timezone.utc),
           "amount": Decimal("1.25"),
           "nested": [{"also": datetime(2026, 3, 4)}],
           "fine": {"n": 5, "ok": True, "none": None}}
    out = st.jsonable(raw)
    json.dumps(out)                       # must not raise
    assert out["when"].startswith("2026-01-02")
    assert out["amount"] == 1.25
    assert out["nested"][0]["also"].startswith("2026-03-04")
    assert out["fine"] == {"n": 5, "ok": True, "none": None}
