"""M9: the guards that make this an experiment and not a comparison.

Everything upstream in this project measures what already happened.
An experiment can say more, and only because of four things that are
easy to state and easy to quietly lose:

1. **The prediction was fixed before the data existed.** Frozen
   fields, checked at readout rather than trusted.
2. **The arms were drawn before the videos were made.** Sorting
   publications into arms afterwards is a post-hoc comparison
   wearing an experiment's clothes.
3. **The stopping rule was honoured.** `read_out` raises rather than
   returning a partial answer, because a number that exists gets
   quoted.
4. **The ad was not the treatment.** M1 exists because a rotating
   $1/day promotion contaminates the outcome.

Lose any one and the result is worth exactly what a correlation is
worth, while looking like something much better. These tests are the
difference.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from server.research import experiment as ex


def _draft(**kw):
    base = dict(title="t", feature="edge_density",
                outcome="reels_skip_rate", platform="instagram",
                predicted_direction="lower", n_per_arm=6)
    base.update(kw)
    return ex.design(**base)


# ---------------------------------------------------------------------------
# 1. the prediction is written in full, before anything
# ---------------------------------------------------------------------------

def test_the_prediction_names_a_direction_and_a_falsifier():
    """A prediction that cannot fail is not a prediction. The text is
    quoted back at readout, so it has to say what being wrong looks
    like."""
    e = _draft()
    assert "lower" in e.prediction
    assert "wrong" in e.prediction.lower()
    assert "other direction" in e.prediction.lower()


def test_the_stopping_rule_forbids_the_two_ways_it_is_broken():
    e = _draft()
    rule = e.stopping_rule.lower()
    assert "do not look before" in rule
    assert "nearly significant" in rule


def test_a_draft_is_not_binding_until_it_is_locked():
    e = _draft()
    assert e.status == "draft"
    assert e.locked_at is None


def test_the_frozen_set_covers_everything_the_prediction_consists_of():
    """If a field that shapes the result is not frozen, it can move
    after the fact and nothing will notice."""
    for field in ("feature", "outcome", "predicted_direction",
                  "n_per_arm", "anchor_age_days", "ad_policy",
                  "analysis", "alpha", "stopping_rule"):
        assert field in ex.FROZEN, field


def test_a_frozen_field_that_moved_is_caught_at_readout():
    e = _draft()
    before = {f: getattr(e, f) for f in ex.FROZEN}
    e.n_per_arm = 3                      # shortened after the fact
    with pytest.raises(ex.Contaminated) as err:
        ex.assert_unchanged(e, before)
    assert "n_per_arm" in str(err.value)
    assert "no longer the one that was registered" in str(err.value)


def test_an_unchanged_experiment_passes_the_check():
    e = _draft()
    before = {f: getattr(e, f) for f in ex.FROZEN}
    ex.assert_unchanged(e, before)       # must not raise


# ---------------------------------------------------------------------------
# 2. the arms are drawn before the videos exist
# ---------------------------------------------------------------------------

class _FakeSession:
    """Enough of a session for `lock`, which only adds rows."""

    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)


def test_locking_draws_every_slot_up_front():
    e = _draft(n_per_arm=5)
    s = _FakeSession()
    rows = ex.lock(s, e, seed=1)
    assert len(rows) == 10
    assert [r.slot for r in rows] == list(range(1, 11))
    assert e.status == "running"
    assert e.locked_at is not None


def test_the_arms_are_balanced_and_blocked_in_pairs():
    """Blocks of two keep the arms balanced if the run is abandoned
    partway, and spread any drift in the owner's playing or the
    platform's behaviour across both arms."""
    e = _draft(n_per_arm=8)
    rows = ex.lock(_FakeSession(), e, seed=7)
    assert sum(1 for r in rows if r.arm == "treatment") == 8
    assert sum(1 for r in rows if r.arm == "control") == 8
    for i in range(0, 16, 2):
        pair = {rows[i].arm, rows[i + 1].arm}
        assert pair == {"treatment", "control"}, i


def test_the_order_is_not_always_the_same():
    a = ex.lock(_FakeSession(), _draft(n_per_arm=10), seed=1)
    b = ex.lock(_FakeSession(), _draft(n_per_arm=10), seed=2)
    assert [r.arm for r in a] != [r.arm for r in b]


def test_a_prediction_is_frozen_once():
    """A second lock would redraw the arms, which is re-rolling the
    dice after seeing them."""
    e = _draft()
    ex.lock(_FakeSession(), e, seed=1)
    with pytest.raises(ValueError, match="frozen once"):
        ex.lock(_FakeSession(), e, seed=2)


# ---------------------------------------------------------------------------
# 3. the stopping rule refuses rather than warns
# ---------------------------------------------------------------------------

def test_not_ready_is_an_exception_rather_than_a_partial_result():
    """Deliberately. Returning a running difference with a caveat
    would mean the number existed, and a number that exists gets
    quoted in a conversation three weeks later with the caveat
    gone."""
    assert issubclass(ex.NotReady, Exception)
    assert issubclass(ex.Contaminated, Exception)


def test_the_refusal_says_how_far_off_it_is():
    e = _draft(n_per_arm=6)
    why = ex._why_not(e, filled=4, mature=4, needed=12)
    assert "8 of 12" in why
    assert "stopping rule" in why.lower()


def test_an_immature_publication_is_not_counted_early():
    """CLAUDE.md 3.8a: a 7-day anchor is read at day 9, because
    Instagram lags up to 48 hours. Comparing a 9-day-old video with a
    3-day-old one measures elapsed exposure, not content."""
    e = _draft()
    why = ex._why_not(e, filled=12, mature=9, needed=12)
    assert "3 publications have not yet reached" in why
    assert "elapsed exposure" in why


def test_the_anchor_carries_the_reporting_lag():
    assert ex.ANCHOR_GRACE_DAYS >= 2


def test_a_completed_experiment_is_not_read_out_twice():
    e = _draft()
    e.status = "complete"
    with pytest.raises(ex.NotReady, match="once"):
        ex.read_out(None, e)


def test_progress_reveals_nothing_about_the_outcome():
    """A progress endpoint that leaked the running difference would
    be a peeking endpoint with a modest name."""
    import inspect
    src = inspect.getsource(ex.progress)
    assert "skip_rate" not in src
    assert "_outcome_values" not in src
    assert "median" not in src


# ---------------------------------------------------------------------------
# 4. the ad is in the design
# ---------------------------------------------------------------------------

def test_only_two_ad_policies_are_analysable():
    from server.db.models import Experiment as E
    ck = next(c for c in E.__table__.constraints
              if getattr(c, "name", "") == "ck_experiments_ad_policy")
    assert "held_constant" in str(ck.sqltext)
    assert "randomised" in str(ck.sqltext)


def test_contamination_is_raised_not_noted():
    """An experiment whose arms were boosted differently measured the
    boost. Reporting it with a caveat would publish a number that
    cannot mean what it appears to."""
    import inspect
    src = inspect.getsource(ex.read_out)
    assert "raise Contaminated" in src


# ---------------------------------------------------------------------------
# the verdict, and what it licenses
# ---------------------------------------------------------------------------

def test_a_significant_result_in_the_wrong_direction_is_not_a_null():
    """The most interesting outcome an experiment can have, and the
    easiest to bury. If the opposite of the prediction happens
    significantly, that is `not_supported`, not `inconclusive`."""
    import inspect
    src = inspect.getsource(ex.read_out)
    assert "significant and not as_predicted" in src
    assert "not_supported" in src


def test_feedback_promotes_only_on_a_supported_result():
    e = _draft()
    e.status, e.verdict = "complete", "supported"
    e.result = {"p_value": 0.01}
    out = ex.feedback(None, e)
    assert out["new_grade"] == "Validated"
    assert "this account" in out["scope"].lower()


def test_a_failed_prediction_retires_the_opportunity():
    e = _draft(opportunity_key="assoc:edge_density:x:instagram")
    e.status, e.verdict, e.result = "complete", "not_supported", {}
    out = ex.feedback(None, e)
    assert out["retire_opportunity"] == "assoc:edge_density:x:instagram"
    assert out["new_grade"] == "Observed"
    # It must not overclaim: the association may still exist.
    assert "may still exist" in out["why"]


def test_inconclusive_is_not_evidence_of_no_effect():
    """CLAUDE.md 3.13. The commonest way to misreport an experiment."""
    e = _draft()
    e.status, e.verdict, e.result = "complete", "inconclusive", {}
    out = ex.feedback(None, e)
    assert out["applies"] is False
    assert "not evidence that there is no effect" in out["why"]


def test_nothing_feeds_back_before_a_verdict():
    e = _draft()
    assert ex.feedback(None, e)["applies"] is False


def test_validated_is_assigned_only_by_the_experiment_loop():
    """The claim `strategist.py` depends on: it enforces "nothing
    causes anything" as absolute today because no other code path
    can put that grade on a feature.

    The scan looks for ASSIGNMENT specifically - `grade = "Validated"`
    or `"grade": "Validated"` - not for the word. Naming the
    threshold is fine and necessary: `strategist.CAUSAL_OK_AT` exists
    precisely so the gate opens by itself when this loop first
    produces one.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2] / "server"
    assigns = re.compile(
        r"""(?:^|[^\w])(?:new_)?grade\s*[:=]\s*["']Validated["']""")
    minting = []
    for path in root.rglob("*.py"):
        if path.name in ("experiment.py", "catalogue.py"):
            continue              # the loop itself, and the ladder
        for line in path.read_text(encoding="utf-8",
                                   errors="replace").splitlines():
            if assigns.search(line):
                minting.append(f"{path.name}: {line.strip()[:70]}")
    assert not minting, (
        "something outside the experiment loop assigns Validated: "
        + "; ".join(minting))


def test_the_strategist_gate_names_the_grade_this_loop_produces():
    """The two halves have to agree, or the gate never opens."""
    from server.ai import strategist as st
    e = _draft()
    e.status, e.verdict, e.result = "complete", "supported", {"p_value": 0.01}
    assert ex.feedback(None, e)["new_grade"] == st.CAUSAL_OK_AT
