"""The generator's two gates: what it can read, and what it must refuse.

Both of these failed in the same live call, which is why they are
tested together. `parse` returned nothing from a perfectly good
reply because the model keyed the array `"hooks"` rather than
`"items"`, and five usable suggestions were thrown away after being
paid for. `review` is the rule layer that a prompt cannot enforce.

The parse cases are not invented shapes. Each one is something the
model actually did, or the obvious neighbour of it: a wrapper named
after the kind, a code fence, a reply cut off by `max_tokens`
mid-object, a brace inside a string. A parser that handles only the
happy path is a parser that silently bills the owner for nothing.
"""

from __future__ import annotations

import json

from server.ai import creative as cr


# ---------------------------------------------------------------------------
# reading the reply
# ---------------------------------------------------------------------------

def test_reads_the_documented_shape():
    text = json.dumps({"items": [{"text": "a"}, {"text": "b"}]})
    assert len(cr.parse(text)) == 2


def test_reads_a_wrapper_named_after_the_kind():
    """The live failure. Asked for hooks, the model returns `hooks`."""
    for key in ("hooks", "ideas", "captions", "suggestions"):
        text = json.dumps({key: [{"text": "a"}, {"text": "b"}]})
        assert len(cr.parse(text)) == 2, key


def test_reads_through_a_code_fence():
    inner = json.dumps({"items": [{"idea": "a"}]})
    assert len(cr.parse(f"```json\n{inner}\n```")) == 1
    assert len(cr.parse(f"```\n{inner}\n```")) == 1


def test_reads_a_bare_list():
    assert len(cr.parse('[{"text": "a"}, {"text": "b"}]')) == 2


def test_reads_json_after_a_preamble():
    inner = json.dumps({"items": [{"text": "a"}]})
    assert len(cr.parse(f"Sure, here you go:\n{inner}\nHope that helps.")) == 1


def test_keeps_the_complete_items_when_the_reply_is_cut_off():
    """`max_tokens` reached mid-object must not cost the good ones.

    The whole point: two finished suggestions survive, and the third,
    which stops mid-string, is dropped rather than guessed at.
    """
    cut = '{"hooks": [{"text": "one"}, {"text": "two"}, {"text": "thr'
    items = cr.parse(cut)
    assert [i["text"] for i in items] == ["one", "two"]


def test_a_brace_inside_a_string_is_not_a_structure():
    text = '{"items": [{"text": "type { to open"}, {"text": "b"}]}'
    assert len(cr.parse(text)) == 2


def test_nothing_readable_yields_nothing():
    for text in ("", "   ", "I cannot help with that.", "null", "42"):
        assert cr.parse(text) == []


def test_never_raises_on_arbitrary_text():
    """A parser that throws turns a bad reply into a 500."""
    for text in ("{", "}", "[", '{"items":', '{"items": [{', "{}" * 50):
        assert isinstance(cr.parse(text), list)


# ---------------------------------------------------------------------------
# refusing what the prompt cannot prevent
# ---------------------------------------------------------------------------

def test_a_promise_about_reach_is_caught():
    bad = json.dumps({"items": [
        {"text": "Play this and it will go viral."}]})
    kinds = {v["kind"] for v in cr.review(bad)}
    assert "virality_promise" in kinds


def test_a_causal_claim_is_caught():
    bad = json.dumps({"items": [
        {"text": "A busier background makes videos perform better."}]})
    kinds = {v["kind"] for v in cr.review(bad)}
    assert "causal_language" in kinds


def test_an_invented_statistic_is_caught():
    for claim in ("This gets 40% more views.",
                  "These get 3x more engagement.",
                  "Studies show that shorter hooks win."):
        kinds = {v["kind"] for v in cr.review(json.dumps(
            {"items": [{"text": claim}]}))}
        assert "invented_statistic" in kinds, claim


def test_an_opus_number_is_not_a_statistic():
    """Numbers are normal in this niche and must not trip the gate."""
    fine = json.dumps({"items": [
        {"text": "Chopin Nocturne op. 9 no. 2, the last 16 bars."},
        {"text": "40 years of practice in 30 seconds."}]})
    assert cr.review(fine) == []


def test_describing_a_measured_pattern_is_allowed():
    """The honest phrasing must survive, or the gate is useless.

    `review` exists to stop a causal claim, not to stop the
    generator from naming the evidence it was given.
    """
    fine = json.dumps({"items": [{
        "text": "Watch the hands.",
        "grounded_in": ("Your breakthroughs tended to have more "
                        "background detail than your other posts.")}]})
    assert cr.review(fine) == []


# ---------------------------------------------------------------------------
# removing the bad suggestion, not the batch
# ---------------------------------------------------------------------------

def test_one_bad_suggestion_does_not_discard_the_good_ones():
    """The live failure on ideas and captions.

    Four clean suggestions and one quoting a percentage used to
    produce an empty page, after all five had been paid for.
    """
    items = [
        {"text": "Watch the hands in the final page."},
        {"text": "This gets you 40% more views."},
        {"text": "Open on the sheet music, then cut to the keys."},
    ]
    kept, rejected = cr.sift(items)
    assert [k["text"] for k in kept] == [items[0]["text"], items[2]["text"]]
    assert len(rejected) == 1
    assert rejected[0]["kind"] == "invented_statistic"


def test_a_rejected_suggestion_names_itself_and_its_words():
    """The owner has to be able to judge the gate, not just trust it."""
    bad = {"text": "Play it slower and it will go viral.", "device": "plain"}
    _, rejected = cr.sift([bad])
    assert rejected[0]["suggestion"] == bad["text"]
    assert "viral" in rejected[0]["excerpt"].lower()


def test_a_broken_rule_in_the_reasoning_still_counts():
    """`grounded_in` is where a stray statistic actually turns up."""
    items = [{"text": "Open on the hands.",
              "grounded_in": "Studies show that close-ups hold attention."}]
    kept, rejected = cr.sift(items)
    assert kept == []
    assert rejected[0]["kind"] == "invented_statistic"
    # It still says which suggestion it was, not just which field.
    assert rejected[0]["suggestion"] == "Open on the hands."


def test_a_clean_batch_passes_through_untouched():
    items = [{"text": "Watch the hands."},
             {"text": "Chopin Nocturne op. 9 no. 2, the last 16 bars."}]
    kept, rejected = cr.sift(items)
    assert kept == items
    assert rejected == []


def test_sifting_nothing_is_not_an_error():
    assert cr.sift([]) == ([], [])


# ---------------------------------------------------------------------------
# the budget, and the thin-brief refusal
# ---------------------------------------------------------------------------

def test_the_token_budget_grows_with_the_count():
    for kind in cr.KINDS:
        assert cr._token_budget(kind, 12) > cr._token_budget(kind, 3)
        assert cr._token_budget(kind, 1) >= 400
        assert cr._token_budget(kind, 50) <= 8000


def test_a_thin_brief_refuses_rather_than_generating():
    """No evidence means no suggestion, not a generic one."""
    assert cr.Brief().is_thin()
    assert cr.Brief(findings=[{"what": "x"}]).is_thin() is False
    assert cr.Brief(best_hooks=[{}, {}, {}]).is_thin() is False
    assert cr.Brief(best_hooks=[{}, {}]).is_thin()
