"""Atomic budget enforcement.

The previous guard read month-to-date spend and compared it to the cap. These
tests cover the four ways that failed, each of which ends in spending money
nobody authorised:

  1. time-of-check to time-of-use between concurrent callers
  2. nothing actually reserved between the check and the charge
  3. a crash after the provider call but before the cost was recorded
  4. a retry paying twice

The concurrency tests use **two real database sessions**, because a race that
only exists between processes cannot be demonstrated with one.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from server import budget as bud
from server import config as cfg
from server.db.models import Base, BudgetReservation

from tests.conftest import TEST_DB_URL, assert_disposable

DB_URL = TEST_DB_URL
pytestmark = pytest.mark.skipif(
    not DB_URL, reason="VIRALLAB_TEST_DB_URL not set")
if DB_URL:
    assert_disposable(DB_URL)

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
PROVIDER = "apify"


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(DB_URL, future=True)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def maker(engine):
    return sessionmaker(engine, expire_on_commit=False, future=True)


@pytest.fixture
def session(maker):
    with maker() as s:
        s.query(BudgetReservation).delete()
        s.commit()
        yield s
        s.rollback()


@pytest.fixture
def authorised(monkeypatch):
    """A $1.00 cap with apify authorised. Nothing is authorised by default,
    so every test that spends must opt in explicitly."""
    def _set(cap=1.00, providers=(PROVIDER,)):
        monkeypatch.setattr(
            cfg, "settings",
            lambda: cfg.Settings(raw={}, spending=cfg.Spending(
                monthly_cap_usd=cap, providers_authorised=tuple(providers))))
    _set()
    return _set


# ---------------------------------------------------------------------------
# the shipped default still refuses
# ---------------------------------------------------------------------------

def test_nothing_is_authorised_by_default(session):
    """`config/settings.yaml` is the real file, not a fixture."""
    assert cfg.load_settings().spending.any_authorised is False
    with pytest.raises(cfg.SpendRefused):
        bud.reserve(session, provider=PROVIDER, estimated_usd=0.30,
                    idempotency_key="k1")


def test_an_unauthorised_provider_is_refused(session, authorised):
    authorised(cap=5.0, providers=("someone-else",))
    with pytest.raises(cfg.SpendRefused, match="apify"):
        bud.reserve(session, provider=PROVIDER, estimated_usd=0.01,
                    idempotency_key="k1")


def test_a_zero_cap_is_refused(session, authorised):
    authorised(cap=0.0)
    with pytest.raises(cfg.SpendRefused, match="0.00"):
        bud.reserve(session, provider=PROVIDER, estimated_usd=0.01,
                    idempotency_key="k1")


def test_an_idempotency_key_is_mandatory(session, authorised):
    """Without one a retry cannot be recognised, and would be billed twice."""
    with pytest.raises(cfg.SpendRefused, match="idempotency"):
        bud.reserve(session, provider=PROVIDER, estimated_usd=0.01,
                    idempotency_key="")


# ---------------------------------------------------------------------------
# 1. concurrency
# ---------------------------------------------------------------------------

def test_two_concurrent_reservations_cannot_both_exceed_the_cap(
        maker, session, authorised, monkeypatch):
    """The original defect, demonstrated with two real sessions.

    $1.00 cap, $0.60 already settled, two callers each estimating $0.30.
    Reading-then-checking lets both pass and the month ends at $1.20.
    """
    authorised(cap=1.00)
    session.add(BudgetReservation(
        provider=PROVIDER, idempotency_key="already", estimated_usd=0.60,
        actual_usd=0.60, status="settled", period=bud.period_of(NOW),
        expires_at=NOW, settled_at=NOW))
    session.commit()

    results: dict[str, object] = {}
    barrier = threading.Barrier(2)

    def attempt(tag: str, key: str):
        with maker() as s:
            # Both threads reach the reservation at the same moment.
            barrier.wait(timeout=20)
            try:
                res = bud.reserve(s, provider=PROVIDER, estimated_usd=0.30,
                                  idempotency_key=key, now=NOW)
                s.commit()
                results[tag] = res
            except cfg.SpendRefused as exc:
                s.rollback()
                results[tag] = exc
            except Exception as exc:                     # noqa: BLE001
                s.rollback()
                results[tag] = exc

    threads = [threading.Thread(target=attempt, args=(t, f"key-{t}"))
               for t in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=40)

    granted = [v for v in results.values()
               if isinstance(v, bud.Reservation)]
    refused = [v for v in results.values()
               if isinstance(v, cfg.SpendRefused)]
    assert len(granted) == 1, (
        f"both callers were granted budget: {results}. The cap would have "
        f"been exceeded.")
    assert len(refused) == 1

    committed = bud.committed_usd(session, PROVIDER, bud.period_of(NOW))
    assert committed["committed_usd"] <= 1.00 + 1e-9
    assert committed["committed_usd"] == pytest.approx(0.90)


def test_concurrent_reservations_with_room_both_succeed(maker, session,
                                                        authorised):
    """The lock must serialise, not block legitimate work."""
    authorised(cap=10.00)
    results = {}
    barrier = threading.Barrier(2)

    def attempt(tag):
        with maker() as s:
            barrier.wait(timeout=20)
            try:
                res = bud.reserve(s, provider=PROVIDER, estimated_usd=0.30,
                                  idempotency_key=f"k-{tag}", now=NOW)
                s.commit()
                results[tag] = res
            except Exception as exc:                     # noqa: BLE001
                s.rollback()
                results[tag] = exc

    threads = [threading.Thread(target=attempt, args=(t,))
               for t in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=40)
    assert all(isinstance(v, bud.Reservation) for v in results.values()), \
        results
    assert bud.committed_usd(
        session, PROVIDER, bud.period_of(NOW))["committed_usd"] == \
        pytest.approx(0.60)


# ---------------------------------------------------------------------------
# 2. budget is actually held
# ---------------------------------------------------------------------------

def test_a_reservation_holds_budget_before_the_call(session, authorised):
    authorised(cap=1.00)
    bud.reserve(session, provider=PROVIDER, estimated_usd=0.80,
                idempotency_key="k1", now=NOW)
    session.commit()
    state = bud.committed_usd(session, PROVIDER, bud.period_of(NOW))
    assert state["reserved_usd"] == pytest.approx(0.80)
    assert state["settled_usd"] == 0.0
    # The held budget is what refuses the next call.
    with pytest.raises(cfg.SpendRefused, match="reserved"):
        bud.reserve(session, provider=PROVIDER, estimated_usd=0.30,
                    idempotency_key="k2", now=NOW)


def test_releasing_gives_the_budget_back(session, authorised):
    authorised(cap=1.00)
    res = bud.reserve(session, provider=PROVIDER, estimated_usd=0.80,
                      idempotency_key="k1", now=NOW)
    bud.release(session, res.id, "provider refused before the request was sent")
    session.commit()
    assert bud.committed_usd(
        session, PROVIDER, bud.period_of(NOW))["committed_usd"] == 0.0
    # And the budget is usable again.
    bud.reserve(session, provider=PROVIDER, estimated_usd=0.80,
                idempotency_key="k2", now=NOW)


def test_settling_records_the_actual_not_the_estimate(session, authorised):
    authorised(cap=5.00)
    res = bud.reserve(session, provider=PROVIDER, estimated_usd=0.30,
                      idempotency_key="k1", now=NOW)
    bud.settle(session, res.id, actual_usd=0.3056)
    session.commit()
    state = bud.committed_usd(session, PROVIDER, bud.period_of(NOW))
    assert state["settled_usd"] == pytest.approx(0.3056)
    assert state["reserved_usd"] == 0.0


def test_an_overshoot_is_recorded_faithfully(session, authorised, caplog):
    """The actual may exceed the estimate. It is recorded as it was billed
    and logged, never clipped to the estimate."""
    authorised(cap=5.00)
    res = bud.reserve(session, provider=PROVIDER, estimated_usd=0.10,
                      idempotency_key="k1", now=NOW)
    with caplog.at_level("WARNING"):
        bud.settle(session, res.id, actual_usd=0.95)
    session.commit()
    row = session.get(BudgetReservation, res.id)
    assert row.actual_usd == pytest.approx(0.95)
    assert "underestimate" in caplog.text.lower()


# ---------------------------------------------------------------------------
# 3. interrupted billing
# ---------------------------------------------------------------------------

def test_an_unsettled_reservation_keeps_holding_budget(session, authorised):
    """A process that died mid-call must not silently free the budget.

    Assuming an unknown call was NOT billed is the assumption that
    overspends, so this goes the other way.
    """
    authorised(cap=1.00)
    bud.reserve(session, provider=PROVIDER, estimated_usd=0.70,
                idempotency_key="crashed", now=NOW)
    session.commit()

    later = NOW + bud.RESERVATION_TTL + timedelta(minutes=5)
    assert bud.expire_stale(session, provider=PROVIDER, now=later) == 1
    session.commit()

    state = bud.committed_usd(session, PROVIDER, bud.period_of(NOW))
    assert state["expired_usd"] == pytest.approx(0.70)
    assert state["committed_usd"] == pytest.approx(0.70), (
        "an expired reservation stopped holding budget")
    row = session.scalars(select(BudgetReservation)).one()
    assert row.status == "expired"
    assert "overspends" in (row.notes or "")


def test_an_expired_reservation_still_refuses_an_overspend(session,
                                                           authorised):
    authorised(cap=1.00)
    bud.reserve(session, provider=PROVIDER, estimated_usd=0.70,
                idempotency_key="crashed", now=NOW)
    session.commit()
    later = NOW + bud.RESERVATION_TTL + timedelta(minutes=5)
    bud.expire_stale(session, provider=PROVIDER, now=later)
    session.commit()
    with pytest.raises(cfg.SpendRefused, match="unresolved"):
        bud.reserve(session, provider=PROVIDER, estimated_usd=0.40,
                    idempotency_key="next", now=later)


def test_a_live_reservation_is_not_expired(session, authorised):
    """A provider run can legitimately take minutes. Slow is not dead."""
    authorised(cap=5.00)
    bud.reserve(session, provider=PROVIDER, estimated_usd=0.30,
                idempotency_key="k1", now=NOW)
    session.commit()
    assert bud.expire_stale(session, provider=PROVIDER,
                            now=NOW + timedelta(minutes=30)) == 0


def test_an_expired_reservation_can_be_resolved_either_way(session,
                                                            authorised):
    authorised(cap=5.00)
    res = bud.reserve(session, provider=PROVIDER, estimated_usd=0.30,
                      idempotency_key="k1", now=NOW)
    session.commit()
    bud.expire_stale(session, provider=PROVIDER,
                     now=NOW + bud.RESERVATION_TTL + timedelta(minutes=1))
    session.commit()
    bud.settle(session, res.id, actual_usd=0.28,
               notes="confirmed from the provider invoice")
    session.commit()
    assert session.get(BudgetReservation, res.id).status == "settled"


# ---------------------------------------------------------------------------
# 4. duplicate requests
# ---------------------------------------------------------------------------

def test_a_retry_with_the_same_key_reuses_the_reservation(session,
                                                           authorised):
    authorised(cap=1.00)
    first = bud.reserve(session, provider=PROVIDER, estimated_usd=0.40,
                        idempotency_key="job-7", now=NOW)
    session.commit()
    second = bud.reserve(session, provider=PROVIDER, estimated_usd=0.40,
                         idempotency_key="job-7", now=NOW)
    session.commit()
    assert second.id == first.id
    assert second.reused is True and first.is_new is True
    assert session.scalar(
        select(func.count(BudgetReservation.id))) == 1
    assert bud.committed_usd(
        session, PROVIDER, bud.period_of(NOW))["committed_usd"] == \
        pytest.approx(0.40), "a retry double-counted the budget"


def test_a_retry_after_settlement_does_not_charge_again(session, authorised):
    authorised(cap=5.00)
    res = bud.reserve(session, provider=PROVIDER, estimated_usd=0.30,
                      idempotency_key="job-7", now=NOW)
    bud.settle(session, res.id, actual_usd=0.30)
    session.commit()
    again = bud.reserve(session, provider=PROVIDER, estimated_usd=0.30,
                        idempotency_key="job-7", now=NOW)
    assert again.reused is True
    assert again.status == "settled"
    assert bud.committed_usd(
        session, PROVIDER, bud.period_of(NOW))["settled_usd"] == \
        pytest.approx(0.30)


def test_settling_twice_is_harmless(session, authorised):
    authorised(cap=5.00)
    res = bud.reserve(session, provider=PROVIDER, estimated_usd=0.30,
                      idempotency_key="k1", now=NOW)
    bud.settle(session, res.id, actual_usd=0.30)
    bud.settle(session, res.id, actual_usd=0.99)
    session.commit()
    assert session.get(BudgetReservation, res.id).actual_usd == \
        pytest.approx(0.30), "a second settle overwrote the recorded charge"


# ---------------------------------------------------------------------------
# accounting integrity
# ---------------------------------------------------------------------------

def test_a_settled_charge_cannot_be_released(session, authorised):
    """Releasing a charge that did happen is how a cap stops meaning
    anything."""
    authorised(cap=5.00)
    res = bud.reserve(session, provider=PROVIDER, estimated_usd=0.30,
                      idempotency_key="k1", now=NOW)
    bud.settle(session, res.id, actual_usd=0.30)
    session.commit()
    with pytest.raises(ValueError, match="settled"):
        bud.release(session, res.id, "oops")


def test_a_released_reservation_cannot_be_settled(session, authorised):
    authorised(cap=5.00)
    res = bud.reserve(session, provider=PROVIDER, estimated_usd=0.30,
                      idempotency_key="k1", now=NOW)
    bud.release(session, res.id, "never sent")
    session.commit()
    with pytest.raises(ValueError, match="released"):
        bud.settle(session, res.id, actual_usd=0.30)


def test_the_period_is_fixed_at_reservation_time(session, authorised):
    """Settling after a month boundary must not move the charge."""
    authorised(cap=5.00)
    res = bud.reserve(session, provider=PROVIDER, estimated_usd=0.30,
                      idempotency_key="k1", now=NOW)
    session.commit()
    bud.settle(session, res.id, actual_usd=0.30)
    session.commit()
    row = session.get(BudgetReservation, res.id)
    assert row.period == "2026-10"
    next_month = datetime(2026, 11, 2, tzinfo=timezone.utc)
    assert bud.committed_usd(
        session, PROVIDER, bud.period_of(next_month))["committed_usd"] == 0.0


def test_spending_exactly_to_the_cap_is_allowed(session, authorised):
    authorised(cap=1.00)
    bud.reserve(session, provider=PROVIDER, estimated_usd=0.75,
                idempotency_key="k1", now=NOW)
    bud.reserve(session, provider=PROVIDER, estimated_usd=0.25,
                idempotency_key="k2", now=NOW)
    session.commit()
    assert bud.committed_usd(
        session, PROVIDER, bud.period_of(NOW))["committed_usd"] == \
        pytest.approx(1.00)


def test_budget_state_reports_what_the_ui_needs(session, authorised):
    authorised(cap=2.00)
    r1 = bud.reserve(session, provider=PROVIDER, estimated_usd=0.50,
                     idempotency_key="k1", now=NOW)
    bud.settle(session, r1.id, actual_usd=0.45)
    bud.reserve(session, provider=PROVIDER, estimated_usd=0.25,
                idempotency_key="k2", now=NOW)
    session.commit()
    state = bud.budget_state(session, now=NOW)
    assert state["monthly_cap_usd"] == 2.00
    assert state["committed_usd"] == pytest.approx(0.70)
    assert state["available_usd"] == pytest.approx(1.30)
    assert state["spending_blocked"] is False
    assert state["by_provider"][PROVIDER]["settled_usd"] == \
        pytest.approx(0.45)


def test_budget_state_says_blocked_when_nothing_is_authorised(session):
    state = bud.budget_state(session, now=NOW)
    assert state["spending_blocked"] is True
    assert state["monthly_cap_usd"] == 0.0
    assert state["providers_authorised"] == []
