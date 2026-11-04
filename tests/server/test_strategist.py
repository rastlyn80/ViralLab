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

import inspect
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
    # The refusal must say what CAN be done, or it is just a
    # wall. Asserted on the offer, not on particular words: the
    # text used to point at "which of your openings differ from
    # your usual ones", which was the whole product when it was
    # written and is now a fraction of it.
    assert "ask me" in out.lower() or "i can tell you" in out.lower()
    assert "what i can" in out.lower() or "i can tell you" in out.lower()


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

def test_the_tool_surface_is_a_closed_list():
    """Seven, and adding an eighth is a decision made here first.

    It was five until the chat was asked what to change about the
    captions and answered that the caption analysis was "not yet
    complete" - with caption length a finding on both sides. None
    of the five could see a finding, so `findings` and
    `what_im_not_doing` were added. The list is asserted whole so
    that growing it stays deliberate.
    """
    assert set(st.TOOLS) == {"findings", "what_im_not_doing",
                             "when_you_post", "how_long",
                             "creator_report", "compare",
                             "publication", "feature_lookup",
                             "evidence_for"}


def test_every_tool_is_declared_to_the_model_with_a_schema():
    declared = {s["name"] for s in st.TOOL_SCHEMAS}
    assert declared == set(st.TOOLS)
    for s in st.TOOL_SCHEMAS:
        assert s["description"]
        assert "properties" in s["input_schema"]
        # A tool whose argument identifies a ROW must say it is
        # required, so the model cannot omit an id and get a silent
        # default row. Arguments that merely narrow an answer -
        # `whose`, `weekday`, `hour` - are optional on purpose:
        # "when should I post" is a real question with no day in
        # it, and forcing one would invent the day.
        props = s["input_schema"]["properties"]
        if props and not s["input_schema"].get("required"):
            for name, spec in props.items():
                assert "enum" in spec or spec["type"] == "integer", (
                    f"{s['name']}.{name} is optional and unconstrained")


def test_no_tool_takes_free_text_that_could_become_a_query():
    """The blueprint forbids the model generating SQL. The defence is
    that no tool accepts anything a query could hide in: every input
    is an integer id or a feature name matched against the
    catalogue."""
    for s in st.TOOL_SCHEMAS:
        for name, spec in s["input_schema"]["properties"].items():
            assert spec["type"] in ("integer", "string"), (s["name"], name)
            if spec["type"] == "string":
                # Either a name checked against the catalogue, or an
                # enum the schema closes. Both end in a value this
                # code chose; neither can carry a query.
                assert (name in ("name", "feature", "outcome")
                        or "enum" in spec), (
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


def test_the_findings_tool_reports_replication_separately():
    """The distinction the whole project rests on, in the tool's shape.

    "Held on your account" and "held on your account and on other
    creators independently" are different claims, and a chat that
    flattens them would overstate the weaker one. So the tool
    cannot return a finding without also saying whether it
    replicated, and the replication list is its own key.

    Asserted on the schema and the contract rather than on live
    data, which belongs to the corpus and not to the test suite.
    """
    schema = next(s for s in st.TOOL_SCHEMAS if s["name"] == "findings")
    assert schema["input_schema"]["properties"]["whose"]["enum"] == [
        "both", "mine", "theirs"]
    src = inspect.getsource(st._findings)
    assert "replicated_on_both" in src
    assert "also_holds_for_other_creators" in src
    assert "contradicts" in src
    # Nothing here is causal, and the tool says so to the model
    # rather than relying on the system prompt alone.
    assert "Nothing here is causal" in src


def test_the_findings_tool_rejects_a_whose_it_does_not_know():
    """Reading an unknown value as "neither" would return the
    replication list alone and read as an empty corpus."""
    with pytest.raises(ValueError):
        st._findings(None, whose="everyone")


def test_the_findings_tool_hands_the_model_a_grade_to_quote():
    """Rule 5 of the system prompt orders the answer to state a
    grade. The first live run of this tool returned rows without
    one, and the model reported two Observed findings as
    "Hypothesised" - it obeyed the instruction with the only grade
    it could produce, which was a guess. A rule the tools cannot
    satisfy gets satisfied by invention."""
    assert "grade" in inspect.getsource(st._findings)
    assert "grade" in inspect.getsource(st._not_doing)
    assert "5. Always give the grade" in st.SYSTEM


@pytest.mark.parametrize("text", [
    "Fewer emoji performs better",
    "Shorter captions work better",
    "Naming the composer does better",
    "Short captions do better",
])
def test_a_rule_about_unpublished_videos_is_still_refused(text):
    assert any(v.kind == "causal_language" for v in st.review(text, []))


@pytest.mark.parametrize("text", [
    "Videos with fewer words tended to do better.",
    "Your shorter captions tend to do better",
    "Naming the form tends to do better",
    "Captions with fewer hashtags did better than the rest",
])
def test_a_report_of_published_videos_is_not(text):
    """The hedge rule 1 of the system prompt orders, allowed by the
    code that enforces rule 1.

    The first live answer built on the `findings` tool said "videos
    with fewer words tended to do better" and was refused - correct
    phrasing, blocked by the pattern meant to catch "fewer emoji
    performs better". Prompt and enforcement have to agree about
    the hedge, not only about the rule.
    """
    assert not [v for v in st.review(text, [])
                if v.kind == "causal_language"]


def test_the_prompt_teaches_the_vocabulary_the_check_allows():
    """A first answer about video length was refused for saying
    "no particular length band performs better than the others".
    That asserts nothing causal — it is a NULL result — but the
    check reads the phrase, not the intent, and `performs
    better` is the phrase.

    Weakening the guard to allow negations would let "no doubt
    shorter videos perform better" through, which is the same
    hole pointing the other way. Teaching the preferred wording
    costs nothing and the tools already return it."""
    assert "separates from the rest" in st.SYSTEM
    assert "nothing performs better" in st.SYSTEM
    # And the guard is untouched.
    assert any(v.kind == "causal_language"
               for v in st.review("Shorter videos perform better", []))
    assert any(v.kind == "causal_language"
               for v in st.review("No length performs better", []))


def test_what_only_other_creators_can_show_is_its_own_category():
    """The owner: "Why are you always considering just my videos
    when in fact the research should be broad and mostly consider
    other people's videos? Because mine have a really went
    viral."

    He is right and the numbers are not close. His 625
    publications: nothing over 100,000 views, nothing over a
    million. The other 9,203, from 331 creators: 1,955 and 654.

    So requiring his account to agree does not only make a claim
    stronger. Where his range cannot show a thing — anything
    about reaching past an audience he already has — it deletes
    it. Eleven measurements are in that position, including cuts
    in the opening and scene changes.
    """
    import inspect

    from server.research import virality as vir

    src = inspect.getsource(st._findings)
    assert "only_on_other_creators" in src
    assert "read_those_first_if_the_question_is_about_reach" in src

    doc = inspect.getdoc(vir.only_on_others) or ""
    assert "no high end" in doc
    assert "deletes it" in doc


@pytest.mark.parametrize("question", [
    "Will this video go viral?",
    "Can you guarantee I blow up?",
    "How do I get millions of views?",
    "Is this going to blow up?",
])
def test_a_request_for_a_prediction_is_still_refused_free(question):
    assert st.precheck(question) is not None


@pytest.mark.parametrize("question", [
    "Do the findings tell me what makes a video go viral, "
    "or just what makes a slightly better video?",
    "What does the evidence show about going viral?",
    "Does the data say anything about virality?",
])
def test_a_question_about_the_evidence_is_not_a_request_for_one(question):
    """The owner asked the first of these and it was refused
    unanswered — the word "viral" was in it, and the guard fires
    before any model is called.

    It is a question about what the project knows, and the
    honest answer had just been built: nothing separates the 390
    publications that reached a hundred thousand people from the
    rest. Refusing it taught him the product could not discuss
    its own limits, which is the opposite of true.

    The difference is whether the question is about the FINDINGS
    or about a VIDEO."""
    assert st.precheck(question) is None


def test_the_refusal_says_what_can_be_asked_instead():
    """It offered "which of your openings differ from your usual
    ones", which was the whole product when it was written and is
    now a fraction of it."""
    said = st.precheck("Will this go viral?")
    assert "331 other creators" in said
    assert "hundred thousand" in said


def test_a_thousands_separator_is_not_an_invented_number():
    """"7,879" matched as "7" and "879", and neither appears in a
    tool result that said 7879 — so a correctly quoted figure was
    shown to the owner as unverified. Every large number the
    model formatted readably had this problem."""
    assert st._numbers("Out of 7,879 publications, 1,070 beat it") == [
        "7879", "1070"]
    assert st._numbers("390 of 7879") == ["390", "7879"]


def test_the_prompt_forbids_the_word_rather_than_the_claim():
    """The guard reads phrases, not intent, so a DENIAL trips it
    exactly as an assertion does: "the findings do not tell you
    what makes a video go viral" was refused as a virality
    promise.

    Weakening the guard to allow negations is the wrong fix —
    "no doubt this will go viral" is a negation too. Telling the
    model not to use the word costs nothing, and the tools
    already return better phrasing."""
    assert "Never write" in st.SYSTEM
    assert "go viral" in st.SYSTEM
    # The phrase wraps in the prompt, so match it unwrapped.
    assert "reached a lot of" in " ".join(st.SYSTEM.split())
    # The guard itself is untouched.
    assert any(v.kind == "virality_promise" for v in st.review(
        "This does not tell you what makes a video go viral", []))


def test_a_tool_that_formats_a_number_still_verifies_it():
    """The comma trap, on the other side of the comparison. A
    tool saying "at least 100,000 views" offered "100" and
    "000" to the checker, so an answer quoting 100,000
    correctly was shown to the owner as invented."""
    call = st.ToolCall(name="x", arguments={},
                       result={"definition": "at least 100,000 views"})
    assert st.unverified_numbers("They passed 100,000.", [call]) == []
    # And a figure the tool really did not return is still caught.
    assert st.unverified_numbers("390 of them passed 100,000.",
                                 [call]) == ["390"]
