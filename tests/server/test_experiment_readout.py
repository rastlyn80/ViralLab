"""The readout, driven end to end on controlled data.

A result that will not exist for months is a result nobody has
tested. `read_out` refuses until the pre-registered sample is posted
and mature, which is correct and means the arithmetic behind the
verdict never runs in anger until the one moment it matters.

So it is run here against publications built to order: an arm that
beats the other, an arm that loses to it, and two arms that cannot be
told apart. The data is synthetic and says nothing about the owner's
videos; what is being tested is that a supported result is recognised
as supported, a reversal is NOT quietly filed as a null, and an
inconclusive run stays inconclusive.

The third is the one worth the fixture. CLAUDE.md 3.13 - an
inconclusive result stays inconclusive - is easy to agree with and
easy to lose to a line of code that treats "not significant" as
"no effect".
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from server.db.models import (Base, Creator, Experiment,
                              ExperimentAssignment, MetricsSnapshot, Video)
from server.research import experiment as ex
from tests.conftest import TEST_DB_URL, assert_disposable

DB_URL = TEST_DB_URL
pytestmark = pytest.mark.skipif(
    not DB_URL, reason="VIRALLAB_TEST_DB_URL not set")
if DB_URL:
    assert_disposable(DB_URL)


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(DB_URL, future=True)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    S = sessionmaker(engine, expire_on_commit=False, future=True)
    with S() as s:
        s.execute(text(
            "truncate experiment_assignments, experiments, "
            "metrics_snapshots, videos, creators restart identity cascade"))
        s.commit()
        yield s
        s.rollback()


def _owner(session):
    c = Creator(platform="instagram", handle="owner",
                is_own_account=True, source="own_api")
    session.add(c)
    session.flush()
    return c


#: Publications in these fixtures repeat skip rates on purpose - a
#: rank test needs ties to be realistic - so the external id cannot
#: be derived from the value. A counter is the only thing here that
#: is actually unique.
_SEQ = itertools.count(1)


def _publication(session, creator, skip_rate: float, *, age_days: int):
    """One publication with a skip rate, posted `age_days` ago."""
    now = datetime.now(timezone.utc)
    posted = now - timedelta(days=age_days)
    v = Video(creator_id=creator.id, platform="instagram",
              external_id=f"exp-fixture-{next(_SEQ)}",
              source="own_api", posted_at=posted, duration_s=30.0,
              media_status="available")
    session.add(v)
    session.flush()
    session.add(MetricsSnapshot(video_id=v.id, captured_at=now,
                                raw={"reels_skip_rate": skip_rate}))
    session.flush()
    return v


def _run(session, treatment: list[float], control: list[float], *,
         age_days: int = 12, direction: str = "lower"):
    """A locked experiment with every slot filled and mature."""
    owner = _owner(session)
    e = ex.design(title="t", feature="edge_density",
                  outcome="reels_skip_rate", platform="instagram",
                  predicted_direction=direction,
                  n_per_arm=len(treatment))
    session.add(e)
    session.flush()
    ex.lock(session, e, seed=1)
    session.flush()

    rows = list(session.scalars(
        session.query(ExperimentAssignment)
        .filter(ExperimentAssignment.experiment_id == e.id)
        .order_by(ExperimentAssignment.slot).statement).all())
    pools = {"treatment": list(treatment), "control": list(control)}
    for r in rows:
        v = _publication(session, owner, pools[r.arm].pop(0),
                         age_days=age_days)
        r.video_id = v.id
        r.filled_at = datetime.now(timezone.utc)
    session.flush()
    return e


# ---------------------------------------------------------------------------
# the three verdicts
# ---------------------------------------------------------------------------

def test_a_prediction_that_comes_true_is_supported(session):
    """Treatment predicted to LOWER the skip rate, and it does, by a
    margin a rank test can see."""
    e = _run(session,
             treatment=[10, 12, 11, 13, 9, 14, 10, 11],
             control=[70, 72, 68, 75, 71, 69, 73, 74])
    out = ex.read_out(session, e)
    assert out.verdict == "supported"
    assert out.detail["observed_direction"] == "lower"
    assert out.detail["p_value"] < 0.05
    assert e.status == "complete"
    # The promise is quoted back rather than paraphrased.
    assert out.detail["prediction_as_written"] == e.prediction


def test_a_reversal_is_not_filed_as_a_null(session):
    """The most interesting outcome an experiment can have, and the
    easiest to bury. The treatment significantly RAISED the skip
    rate, which is not "we found nothing"."""
    e = _run(session,
             treatment=[70, 72, 68, 75, 71, 69, 73, 74],
             control=[10, 12, 11, 13, 9, 14, 10, 11])
    out = ex.read_out(session, e)
    assert out.verdict == "not_supported"
    assert out.detail["observed_direction"] == "higher"
    assert out.detail["significant"] is True


def test_two_arms_that_cannot_be_told_apart_stay_inconclusive(session):
    """CLAUDE.md 3.13. "Not significant" is not "no effect", and the
    verdict must not say it is."""
    e = _run(session,
             treatment=[40, 42, 38, 45, 41, 39, 43, 44],
             control=[41, 43, 39, 44, 40, 42, 38, 45])
    out = ex.read_out(session, e)
    assert out.verdict == "inconclusive"
    fb = ex.feedback(session, e)
    assert fb["applies"] is False
    assert "not evidence that there is no effect" in fb["why"]


# ---------------------------------------------------------------------------
# the stopping rule, where it bites
# ---------------------------------------------------------------------------

def test_an_unfinished_run_refuses_rather_than_answering(session):
    e = _run(session, treatment=[10, 12, 11, 13], control=[70, 72, 68, 75])
    # Empty two slots: the run is no longer complete.
    rows = list(session.scalars(
        session.query(ExperimentAssignment)
        .filter(ExperimentAssignment.experiment_id == e.id)
        .limit(2).statement).all())
    for r in rows:
        r.video_id = None
    session.flush()
    with pytest.raises(ex.NotReady, match="still to post"):
        ex.read_out(session, e)
    assert e.status == "running"
    assert e.verdict is None


def test_publications_too_young_to_read_are_refused(session):
    """A 7-day anchor is read at day 9. Reading a 3-day-old video
    against a 9-day-old one measures elapsed exposure."""
    e = _run(session, treatment=[10, 12, 11, 13],
             control=[70, 72, 68, 75], age_days=3)
    with pytest.raises(ex.NotReady, match="have not yet reached"):
        ex.read_out(session, e)


def test_a_result_is_computed_once(session):
    e = _run(session,
             treatment=[10, 12, 11, 13, 9, 14, 10, 11],
             control=[70, 72, 68, 75, 71, 69, 73, 74])
    ex.read_out(session, e)
    with pytest.raises(ex.NotReady, match="once"):
        ex.read_out(session, e)


def test_a_moved_prediction_voids_the_run(session):
    """The whole exercise is worthless if the registered prediction
    can change afterwards, and every page would still look fine."""
    e = _run(session,
             treatment=[10, 12, 11, 13, 9, 14, 10, 11],
             control=[70, 72, 68, 75, 71, 69, 73, 74])
    before = {f: getattr(e, f) for f in ex.FROZEN}
    e.predicted_direction = "higher"      # flipped after the fact
    with pytest.raises(ex.Contaminated, match="predicted_direction"):
        ex.read_out(session, e, frozen_before=before)


# ---------------------------------------------------------------------------
# progress leaks nothing
# ---------------------------------------------------------------------------

def test_progress_shows_how_far_along_and_no_outcome(session):
    e = _run(session,
             treatment=[10, 12, 11, 13, 9, 14, 10, 11],
             control=[70, 72, 68, 75, 71, 69, 73, 74])
    p = ex.progress(session, e)
    assert p["filled"] == 16 and p["slots"] == 16
    assert p["can_read_out"] is True
    blob = repr(p)
    for leaked in ("70", "10", "median", "p_value"):
        assert leaked not in blob, f"progress leaked {leaked!r}"


def test_feedback_after_a_supported_result_is_the_only_route_to_validated(
        session):
    e = _run(session,
             treatment=[10, 12, 11, 13, 9, 14, 10, 11],
             control=[70, 72, 68, 75, 71, 69, 73, 74])
    ex.read_out(session, e)
    fb = ex.feedback(session, e)
    assert fb["new_grade"] == "Validated"
    assert fb["feature"] == "edge_density"
    assert "this account" in fb["scope"].lower()
