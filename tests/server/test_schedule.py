"""Work that happens without anyone pressing anything.

The owner asked twice for a platform that does its own research.
A scheduler is the obvious answer and the obvious hazard, so the
tests are mostly about what it refuses to do.

The rule that matters: **a scheduled operation may not cost
money.** Not capped, not cheap - free. An overnight loop that
spends is the worst thing this module could contain, and no
ceiling turns an unattended charge into something the owner
agreed to.
"""

from __future__ import annotations

import datetime as dt

import pytest

from server.ops import catalogue as cat
from server.ops import schedule as sched


# ---------------------------------------------------------------------------
# nothing automatic may cost money
# ---------------------------------------------------------------------------

def test_nothing_on_the_schedule_costs_money():
    for kind, _hours in sched.SCHEDULE:
        assert not cat.get(kind).costs_money, kind


def test_adding_a_paid_operation_is_refused():
    paid = next(o.key for o in cat.CATALOGUE if o.costs_money)
    with pytest.raises(sched.Paid):
        sched._free(paid)


def test_the_refusal_happens_at_import_not_at_run():
    """A scheduler that only refuses when the clock strikes is a
    scheduler that refuses in the middle of the night."""
    import inspect
    src = inspect.getsource(sched)
    assert "_validate()" in src
    assert src.index("def _validate") < src.index("\n_validate()")


def test_the_long_unattended_pass_is_deliberately_not_scheduled():
    """`watch_competitors` is free and still must not run alone.

    It talks to someone else's servers for hours, and that is the
    one thing ADR 0010's politeness argument cannot survive.
    """
    assert "watch_competitors" not in {k for k, _ in sched.SCHEDULE}


# ---------------------------------------------------------------------------
# when something is due
# ---------------------------------------------------------------------------

class _Session:
    def __init__(self, last=None, running=()):
        self._last, self._running = last, running


def _patch(monkeypatch, *, last, running=()):
    monkeypatch.setattr(sched, "last_success", lambda s, k: last)
    monkeypatch.setattr(sched.runner, "live",
                        lambda s, kind=None: [
                            type("O", (), {"kind": k})() for k in running])


def test_never_run_means_due(monkeypatch):
    _patch(monkeypatch, last=None)
    assert set(sched.due(None)) == {k for k, _ in sched.SCHEDULE}


def test_run_an_hour_ago_is_not_due(monkeypatch):
    now = dt.datetime(2026, 11, 1, 12, tzinfo=dt.timezone.utc)
    _patch(monkeypatch, last=now - dt.timedelta(hours=1))
    assert sched.due(None, now=now) == []


def test_run_two_days_ago_is_due(monkeypatch):
    now = dt.datetime(2026, 11, 1, 12, tzinfo=dt.timezone.utc)
    _patch(monkeypatch, last=now - dt.timedelta(days=2))
    assert sched.due(None, now=now)


def test_already_running_is_not_due(monkeypatch):
    now = dt.datetime(2026, 11, 1, 12, tzinfo=dt.timezone.utc)
    _patch(monkeypatch, last=None, running=("read_hooks",))
    assert "read_hooks" not in sched.due(None, now=now)


def test_a_naive_timestamp_does_not_crash_the_comparison(monkeypatch):
    """Postgres can hand back a naive datetime, and comparing it
    with an aware one raises - inside a loop that must not die."""
    now = dt.datetime(2026, 11, 1, 12, tzinfo=dt.timezone.utc)
    _patch(monkeypatch, last=dt.datetime(2026, 10, 1, 12))
    assert sched.due(None, now=now)


def test_a_week_offline_does_not_queue_seven_runs(monkeypatch):
    """Nothing accumulates: each kind is due once, not once per
    missed day."""
    now = dt.datetime(2026, 11, 8, 12, tzinfo=dt.timezone.utc)
    _patch(monkeypatch, last=now - dt.timedelta(days=7))
    due = sched.due(None, now=now)
    assert len(due) == len(set(due))


# ---------------------------------------------------------------------------
# the loop survives itself
# ---------------------------------------------------------------------------

def test_a_failing_tick_does_not_kill_the_loop():
    """A scheduler that dies on one bad tick is worse than none,
    because the page keeps saying the work is automatic."""
    import inspect
    src = inspect.getsource(sched.loop)
    assert "except Exception" in src
    assert "CancelledError" in src


def test_it_is_wired_into_startup():
    import inspect
    from server.api import app as app_module
    assert "ops_schedule.loop" in inspect.getsource(app_module.lifespan)
