"""The spending guard: the single door every paid call goes through.

`spending.monthly_cap_usd` sat in configuration from the start and was read
by nothing. Phase 4 discovery needs a paid provider, because official APIs
expose no competitor data, so this is now the gate that enforces CLAUDE.md
§7: never silently introduce a paid dependency, never silently exceed a
configured limit.

The default posture is **refuse**. These tests exist to make sure that stays
true by accident as well as by intent.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server import config as cfg
from server.db.models import Base, CollectionRun

from tests.conftest import TEST_DB_URL, assert_disposable

DB_URL = TEST_DB_URL
pytestmark = pytest.mark.skipif(
    not DB_URL, reason="VIRALLAB_TEST_DB_URL not set")
if DB_URL:
    assert_disposable(DB_URL)

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def maker():
    eng = create_engine(DB_URL, future=True)
    Base.metadata.create_all(eng)
    yield sessionmaker(eng, expire_on_commit=False, future=True)
    eng.dispose()


@pytest.fixture
def session(maker):
    with maker() as s:
        s.query(CollectionRun).delete()
        s.commit()
        yield s
        s.rollback()


def authorise(monkeypatch, *, cap: float, providers=("apify",)):
    monkeypatch.setattr(
        cfg, "settings",
        lambda: cfg.Settings(raw={}, spending=cfg.Spending(
            monthly_cap_usd=cap, providers_authorised=tuple(providers))))


def add_run(session, cost, *, when=NOW, provider="apify"):
    run = CollectionRun(platform="tiktok", account_ref="a", kind="discovery",
                        started_at=when, status="ok", provider=provider,
                        cost_usd=cost)
    session.add(run)
    session.commit()
    return run


# ---------------------------------------------------------------------------
# the shipped default: nothing is authorised
# ---------------------------------------------------------------------------

def test_the_shipped_configuration_authorises_nothing():
    """`config/settings.yaml` is the real file here, not a fixture."""
    s = cfg.load_settings()
    assert s.spending.monthly_cap_usd == 0.0
    assert s.spending.providers_authorised == ()
    assert s.spending.any_authorised is False


def test_a_paid_call_is_refused_by_default(session):
    with pytest.raises(cfg.SpendRefused):
        cfg.require_spend(session, provider="apify", estimated_usd=0.30)


def test_a_missing_settings_file_authorises_nothing(tmp_path):
    s = cfg.load_settings(tmp_path / "nope.yaml")
    assert s.spending.any_authorised is False


# ---------------------------------------------------------------------------
# each gate refuses for a distinguishable reason
# ---------------------------------------------------------------------------

def test_an_unauthorised_provider_is_named_in_the_refusal(session,
                                                          monkeypatch):
    authorise(monkeypatch, cap=10.0, providers=("apify",))
    with pytest.raises(cfg.SpendRefused, match="some-other-provider"):
        cfg.require_spend(session, provider="some-other-provider",
                          estimated_usd=0.01)


def test_a_zero_cap_refuses_even_an_authorised_provider(session, monkeypatch):
    authorise(monkeypatch, cap=0.0, providers=("apify",))
    with pytest.raises(cfg.SpendRefused, match="0.00"):
        cfg.require_spend(session, provider="apify", estimated_usd=0.01)


def test_exceeding_the_cap_is_refused_and_says_by_how_much(session,
                                                           monkeypatch):
    authorise(monkeypatch, cap=1.00)
    add_run(session, 0.90)
    with pytest.raises(cfg.SpendRefused) as exc:
        cfg.require_spend(session, provider="apify", estimated_usd=0.25,
                          now=NOW)
    msg = str(exc.value)
    assert "1.00" in msg and "0.9" in msg
    assert "approval" in msg


def test_a_call_within_the_cap_is_allowed(session, monkeypatch):
    authorise(monkeypatch, cap=5.00)
    add_run(session, 1.50)
    decision = cfg.require_spend(session, provider="apify",
                                 estimated_usd=0.30, now=NOW)
    assert decision["spent_before_usd"] == pytest.approx(1.50)
    assert decision["projected_usd"] == pytest.approx(1.80)
    assert decision["cap_usd"] == 5.00


def test_spending_exactly_to_the_cap_is_allowed(session, monkeypatch):
    """The cap is a limit, not an exclusive bound - refusing the last cent
    would be surprising."""
    authorise(monkeypatch, cap=1.00)
    add_run(session, 0.75)
    cfg.require_spend(session, provider="apify", estimated_usd=0.25, now=NOW)


def test_a_negative_estimate_is_refused(session, monkeypatch):
    authorise(monkeypatch, cap=5.00)
    with pytest.raises(cfg.SpendRefused):
        cfg.require_spend(session, provider="apify", estimated_usd=-1.0)


# ---------------------------------------------------------------------------
# the budget window
# ---------------------------------------------------------------------------

def test_only_the_current_month_counts_against_the_cap(session, monkeypatch):
    authorise(monkeypatch, cap=1.00)
    add_run(session, 0.95, when=NOW - timedelta(days=40))   # last month
    decision = cfg.require_spend(session, provider="apify",
                                 estimated_usd=0.50, now=NOW)
    assert decision["spent_before_usd"] == pytest.approx(0.0)


def test_runs_with_no_recorded_cost_do_not_break_the_sum(session,
                                                         monkeypatch):
    """Official-API runs have a NULL cost. They must count as zero, not as
    an error."""
    authorise(monkeypatch, cap=1.00)
    add_run(session, None, provider=None)
    add_run(session, 0.10)
    assert cfg.spent_this_month(session, now=NOW) == pytest.approx(0.10)


def test_budget_state_reports_what_the_ui_needs(session, monkeypatch):
    authorise(monkeypatch, cap=2.00)
    add_run(session, 0.50)
    state = cfg.budget_state(session, now=NOW)
    assert state["monthly_cap_usd"] == 2.00
    assert state["spent_this_month_usd"] == pytest.approx(0.50)
    assert state["remaining_usd"] == pytest.approx(1.50)
    assert state["providers_authorised"] == ["apify"]
    assert state["any_authorised"] is True


def test_remaining_never_goes_negative(session, monkeypatch):
    authorise(monkeypatch, cap=1.00)
    add_run(session, 3.00)
    assert cfg.budget_state(session, now=NOW)["remaining_usd"] == 0.0


# ---------------------------------------------------------------------------
# recording actual cost
# ---------------------------------------------------------------------------

def test_record_spend_attaches_the_actual_cost(session, monkeypatch):
    authorise(monkeypatch, cap=5.00)
    run = CollectionRun(platform="tiktok", account_ref="a", kind="discovery",
                        started_at=NOW, status="running", provider="apify")
    session.add(run)
    session.commit()
    cfg.record_spend(session, run, 0.3056)
    session.commit()
    assert cfg.spent_this_month(session, now=NOW) == pytest.approx(0.3056)


def test_refusal_is_not_a_transient_error():
    """`SpendRefused` must not be retryable: a retry loop against a budget
    refusal would be both useless and alarming."""
    assert issubclass(cfg.SpendRefused, RuntimeError)
    import httpx
    assert not issubclass(cfg.SpendRefused, httpx.HTTPError)
