"""Did the advice work - and does the module admit what it cannot say?

The suggestions table shipped with a `used_video_id` column and a
comment promising that attaching a publication turns "did our advice
work" into a query. `research/did_it_work.py` is that query.

The risk here is not an arithmetic error. It is that a run of four
good results gets reported as the product working, when the owner
chose which suggestions to film and would have beaten his own
baseline on taste alone. So most of these tests are about the
refusals: the threshold below which there is no verdict, and the
self-selection caveat that has to travel with every answer.
"""

from __future__ import annotations

from server.research import did_it_work as diw


# ---------------------------------------------------------------------------
# the arithmetic
# ---------------------------------------------------------------------------

def test_the_sign_test_is_an_exact_two_sided_binomial():
    assert round(diw._sign_test_p(8, 0), 5) == 0.00781
    assert round(diw._sign_test_p(12, 0), 5) == 0.00049
    assert diw._sign_test_p(6, 6) == 1.0
    assert diw._sign_test_p(5, 4) == 1.0


def test_it_is_symmetric():
    """Direction is read off the counts, never off the p-value."""
    assert diw._sign_test_p(9, 2) == diw._sign_test_p(2, 9)


def test_nothing_posted_is_not_a_p_value_of_one():
    assert diw._sign_test_p(0, 0) is None


def test_it_agrees_with_the_other_sign_test_in_the_project():
    """Two modules, one piece of arithmetic, written out twice.

    Duplicated deliberately - see the docstring in
    `_sign_test_p` - which makes it worth checking they have not
    drifted apart.
    """
    from server.research import matched_pairs as mp

    for up, down in ((0, 0), (1, 0), (3, 7), (9, 1), (11, 11)):
        assert diw._sign_test_p(up, down) == mp._sign_test_p(up, down)


# ---------------------------------------------------------------------------
# the stopping rule
# ---------------------------------------------------------------------------

def test_a_short_run_is_counted_and_not_judged():
    """Four of five is what a fair coin does one time in five.

    The threshold is in code rather than in a docstring so that it
    cannot be relaxed on the day the number is nearly interesting.
    """
    assert diw.MIN_TO_SAY_ANYTHING >= 10
    verdict = diw._verdict(4, 1, diw._sign_test_p(4, 1))
    assert "counted and not judged" in verdict
    assert str(diw.MIN_TO_SAY_ANYTHING) in verdict
    assert "before there was any data" in verdict


def test_nothing_posted_says_what_to_do_about_it():
    verdict = diw._verdict(0, 0, None)
    assert "Kept" in verdict
    assert "p =" not in verdict


def test_a_result_that_clears_the_bar_still_refuses_the_credit():
    """The whole point. A significant win is a win for his choosing
    and the suggestions together, and the sentence says so rather
    than leaving the reader to infer it."""
    verdict = diw._verdict(9, 1, diw._sign_test_p(9, 1))
    assert "you chose which to use" in verdict
    assert "not that the suggestions did" in verdict


def test_a_bad_result_is_reported_as_the_one_that_counts():
    """The asymmetry is real: self-selection cannot explain away a
    loss, so a loss is the stronger evidence and is labelled that
    way."""
    verdict = diw._verdict(1, 9, diw._sign_test_p(1, 9))
    assert "counts against the advice" in verdict
    assert "you chose the ones you believed in" in verdict


def test_an_inconclusive_run_past_the_threshold_stays_inconclusive():
    verdict = diw._verdict(6, 5, diw._sign_test_p(6, 5))
    assert "Not separable from chance" in verdict


# ---------------------------------------------------------------------------
# what the caller is handed
# ---------------------------------------------------------------------------

def test_better_than_usual_means_better_than_usual_not_a_breakout():
    """`virality.breakthroughs` asks whether a publication cleared
    its baseline by 3.5 deviations. This asks whether it did better
    than typical at all, which is a different and much less
    demanding question, and the constant says which."""
    assert diw.BEAT_AT_Z == 0.0


def test_the_caveats_name_every_reason_this_is_not_the_experiment():
    out = diw.review.__doc__ or ""
    assert out                                 # it has one
    reasons = " ".join(_caveats())
    assert "chose which suggestions" in reasons
    assert "chose when to post" in reasons
    assert "no control arm" in reasons.lower()
    assert "pre-registered experiment" in reasons


def _caveats() -> list[str]:
    """Pulled from the module rather than retyped, so a caveat that
    is deleted fails this instead of quietly disappearing."""
    import inspect

    src = inspect.getsource(diw.review)
    start = src.index('"why_this_is_not_proof"')
    end = src.index('"what_it_is_good_for"')
    return [src[start:end]]


def test_a_review_with_no_rows_does_not_touch_the_baseline(monkeypatch):
    """Scoring every own publication is the expensive half. A page
    opened before anything was ever marked as posted should not pay
    for it.
    """
    called = {"n": 0}

    def _boom(session):
        called["n"] += 1
        return {}

    monkeypatch.setattr(diw, "_scored_own", _boom)

    class _Session:
        def execute(self, *_a, **_k):
            class _R:
                @staticmethod
                def all():
                    return []
            return _R()

    out = diw.review(_Session())
    assert called["n"] == 0
    assert out["scored"] == 0
    assert out["p_value"] is None
    assert out["grade"] == "Observed"
    assert "Kept" in out["verdict"]


def test_the_null_the_sign_test_assumes_is_recorded():
    """A 50% null is not an assumption here, it is a measurement:
    287 of 562 own publications score above their own trailing
    window. If that drifts, this test is measuring the baseline
    rather than the advice, so the figure and its date are in the
    module where a reader will find them."""
    doc = diw.__doc__ or ""
    assert "287 of 562" in doc
    assert "2026-11-01" in doc
    assert "re-checking if the baseline window ever changes" in doc
