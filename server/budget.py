"""Atomic budget enforcement for paid provider calls.

`server/config.py` reads the configured cap. This module is what makes it
binding. Every paid call goes:

    res = reserve(session, provider="apify", estimated_usd=0.30,
                  idempotency_key=f"discovery:{run_id}")
    try:
        actual = provider.call(...)          # money may now be spent
    except Exception:
        release(session, res.id, "provider call failed before billing")
        raise
    else:
        settle(session, res.id, actual_usd=actual)

Four properties the previous read-and-compare guard did not have:

**Atomicity.** `reserve()` takes a transaction-scoped advisory lock keyed on
(provider, month) before it sums anything, so two concurrent callers cannot
both pass the same check. The lock is per budget period, not global, so
unrelated work is unaffected, and it releases on commit or rollback without
any cleanup path to forget.

**Idempotency.** A retry presenting the same key gets the original
reservation back instead of a second charge. The unique constraint is the
real guarantee; the pre-check is just a fast path.

**Crash safety.** The reservation exists before the money is at risk, so a
process that dies mid-call leaves evidence. It keeps holding budget until
someone settles or releases it.

**Honest accounting.** Committed spend is settled actuals plus reserved
estimates plus expired estimates - never just the actuals, because an
in-flight or unknown call is still money that may have left.

A reservation whose outcome is unknown (`expired`) deliberately keeps
holding budget. Assuming an unknown call was NOT billed is the assumption
that overspends, so this module assumes the opposite and makes the operator
resolve it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server import config
from server.config import SpendRefused, month_start
from server.db.models import BudgetReservation

logger = logging.getLogger("virallab.budget")

#: How long a reservation may stay open before its outcome is treated as
#: unknown. Generous: a provider run can legitimately take many minutes, and
#: declaring a live call dead would release budget it is still using.
RESERVATION_TTL = timedelta(hours=2)

#: Statuses that still hold budget. `expired` is included on purpose.
HOLDING = ("reserved", "expired")


@dataclass(frozen=True)
class Reservation:
    id: int
    provider: str
    idempotency_key: str
    estimated_usd: float
    status: str
    period: str
    reused: bool = False

    @property
    def is_new(self) -> bool:
        return not self.reused


def period_of(now: datetime | None = None) -> str:
    return month_start(now).strftime("%Y-%m")


#: The spending categories. A charge must belong to exactly one, and a
#: category that is not configured is NOT authorised - see
#: `config.Spending.cap_for`.
CATEGORIES = ("discovery", "cloud_vision", "cloud_audio", "strategy")


def _lock_period(session: Session, provider: str, period: str,
                 category: str = "discovery") -> None:
    """Serialise budget decisions for one provider-month.

    A transaction-scoped advisory lock, so it is released by commit or
    rollback with no unlock path that could be missed. Two lock arguments
    rather than a hashed string, to keep collisions between unrelated keys
    out of the picture.
    """
    session.execute(
        text("select pg_advisory_xact_lock(hashtext(:a), hashtext(:b))"),
        {"a": f"virallab-budget:{provider}:{category}", "b": period})


def committed_usd(session: Session, provider: str, period: str,
                  category: str | None = None) -> dict:
    """What the budget currently owes, broken down.

    Settled actuals, plus estimates for anything still open or unresolved.
    Reporting the parts separately matters: "spent" and "committed but not
    yet charged" are different facts about the same budget.
    """
    stmt = (
        select(BudgetReservation.status,
               func.coalesce(func.sum(BudgetReservation.actual_usd), 0.0),
               func.coalesce(func.sum(BudgetReservation.estimated_usd), 0.0))
        .where(BudgetReservation.provider == provider,
               BudgetReservation.period == period))
    if category is not None:
        # One category must not be able to consume another's allowance.
        stmt = stmt.where(BudgetReservation.category == category)
    rows = session.execute(stmt.group_by(BudgetReservation.status)).all()
    settled = reserved = expired = 0.0
    for status, actual, estimate in rows:
        if status == "settled":
            settled += float(actual or 0.0)
        elif status == "reserved":
            reserved += float(estimate or 0.0)
        elif status == "expired":
            expired += float(estimate or 0.0)
        # 'released' holds nothing by definition.
    total = settled + reserved + expired
    return {"settled_usd": round(settled, 4),
            "reserved_usd": round(reserved, 4),
            "expired_usd": round(expired, 4),
            "committed_usd": round(total, 4)}


def expire_stale(session: Session, *, provider: str | None = None,
                 now: datetime | None = None) -> int:
    """Mark reservations whose outcome is unknown.

    They keep holding budget. This only changes the label, so the operator
    can see that something needs resolving rather than discovering a
    mystery charge later.

    The comparison is against `expires_at`, which already encodes the
    deadline chosen when the reservation was taken. Subtracting the TTL
    again here would double-count it and never expire anything.
    """
    stamp = now or datetime.now(timezone.utc)
    stmt = (select(BudgetReservation)
            .where(BudgetReservation.status == "reserved",
                   BudgetReservation.expires_at < stamp))
    if provider:
        stmt = stmt.where(BudgetReservation.provider == provider)
    rows = session.scalars(stmt).all()
    for r in rows:
        r.status = "expired"
        r.notes = ((r.notes or "") +
                   " | outcome unknown: no settle or release before the TTL. "
                   "Still holding budget, because assuming it was not billed "
                   "is the assumption that overspends.")
    if rows:
        session.flush()
        logger.warning("%d budget reservation(s) expired with unknown "
                       "outcome; they continue to hold budget", len(rows))
    return len(rows)


def reserve(session: Session, *, provider: str, estimated_usd: float,
            idempotency_key: str, category: str = "discovery",
            now: datetime | None = None,
            run_id: int | None = None, ttl: timedelta = RESERVATION_TTL,
            detail: dict | None = None) -> Reservation:
    """Claim budget for one paid call, or raise `SpendRefused`.

    Commits nothing: the caller owns the transaction, so a reservation and
    whatever the caller records alongside it land together or not at all.
    """
    cfg = config.settings().spending
    if category not in CATEGORIES:
        raise SpendRefused(
            f"unknown spending category {category!r}; expected one of "
            f"{list(CATEGORIES)}")
    if provider not in cfg.providers_authorised:
        raise SpendRefused(
            f"provider {provider!r} is not in providers_authorised "
            f"{list(cfg.providers_authorised)}; authorise it in "
            f"config/settings.yaml before any paid call")
    if cfg.monthly_cap_usd <= 0:
        raise SpendRefused(
            "spending.monthly_cap_usd is 0.00, so no paid service is "
            "authorised")
    category_cap = cfg.cap_for(category)
    if category_cap <= 0:
        raise SpendRefused(
            f"the {category!r} spending category has no configured cap, so "
            f"it is not authorised. An unconfigured category means 'not "
            f"permitted', not 'unlimited' - authorising discovery must not "
            f"silently authorise cloud inference. Add it to "
            f"spending.category_caps_usd in config/settings.yaml.")
    if estimated_usd < 0:
        raise SpendRefused("a negative estimated cost is not meaningful")
    if not idempotency_key:
        # Without one, a retry cannot be recognised and would pay twice.
        raise SpendRefused("an idempotency key is required: without one a "
                           "retried call cannot be distinguished from a new "
                           "one, and would be billed twice")

    stamp = now or datetime.now(timezone.utc)
    period = period_of(stamp)

    _lock_period(session, provider, period, category)

    existing = session.scalar(
        select(BudgetReservation).where(
            BudgetReservation.provider == provider,
            BudgetReservation.idempotency_key == idempotency_key))
    if existing is not None:
        logger.info("reusing budget reservation %s for %s/%s (status %s)",
                    existing.id, provider, idempotency_key, existing.status)
        return Reservation(
            id=existing.id, provider=existing.provider,
            idempotency_key=existing.idempotency_key,
            estimated_usd=existing.estimated_usd, status=existing.status,
            period=existing.period, reused=True)

    expire_stale(session, provider=provider, now=stamp)

    # TWO ceilings, both enforced: the category's own and the overall cap.
    # The category check comes first because it is the tighter and more
    # specific statement of intent.
    cat_state = committed_usd(session, provider, period, category)
    cat_projected = cat_state["committed_usd"] + estimated_usd
    if cat_projected > category_cap + 1e-9:
        # The breakdown matters as much here as in the global message: this
        # check fires first and would otherwise shadow it with less
        # information whenever the two caps coincide.
        raise SpendRefused(
            f"this call would commit ${cat_projected:.4f} to {category!r} in "
            f"{period}, over that category's ${category_cap:.2f} cap. "
            f"Already committed to {category!r}: "
            f"${cat_state['committed_usd']:.4f} "
            f"(${cat_state['settled_usd']:.4f} settled, "
            f"${cat_state['reserved_usd']:.4f} reserved, "
            f"${cat_state['expired_usd']:.4f} unresolved). Another "
            f"category's remaining allowance cannot be borrowed for this "
            f"one, and overage requires explicit approval rather than an "
            f"automatic charge.")

    state = committed_usd(session, provider, period)
    projected = state["committed_usd"] + estimated_usd
    if projected > cfg.monthly_cap_usd + 1e-9:
        raise SpendRefused(
            f"this call would commit ${projected:.4f} in {period}, over the "
            f"${cfg.monthly_cap_usd:.2f} cap. Already committed: "
            f"${state['committed_usd']:.4f} "
            f"(${state['settled_usd']:.4f} settled, "
            f"${state['reserved_usd']:.4f} reserved, "
            f"${state['expired_usd']:.4f} unresolved). Overage requires "
            f"explicit approval, never an automatic charge")

    row = BudgetReservation(
        provider=provider, idempotency_key=idempotency_key, category=category,
        estimated_usd=float(estimated_usd), status="reserved", period=period,
        expires_at=stamp + ttl, run_id=run_id, detail=detail)
    session.add(row)
    try:
        session.flush()
    except IntegrityError:
        # Lost a race to the unique constraint despite the lock - possible if
        # a caller reserved outside it. The constraint is the real guarantee,
        # so fall back to the existing row rather than charging twice.
        session.rollback()
        _lock_period(session, provider, period, category)
        again = session.scalar(
            select(BudgetReservation).where(
                BudgetReservation.provider == provider,
                BudgetReservation.idempotency_key == idempotency_key))
        if again is None:
            raise
        return Reservation(
            id=again.id, provider=again.provider,
            idempotency_key=again.idempotency_key,
            estimated_usd=again.estimated_usd, status=again.status,
            period=again.period, reused=True)

    logger.info("reserved $%.4f of %s budget for %s (%s); committed now "
                "$%.4f of $%.2f", estimated_usd, provider, idempotency_key,
                period, projected, cfg.monthly_cap_usd)
    return Reservation(
        id=row.id, provider=provider, idempotency_key=idempotency_key,
        estimated_usd=float(estimated_usd), status="reserved", period=period)


def settle(session: Session, reservation_id: int, *, actual_usd: float,
           run_id: int | None = None, notes: str | None = None) -> None:
    """Record what the provider actually billed.

    The actual may legitimately differ from the estimate in either
    direction, and the actual is what the plan requires us to report
    (CLAUDE.md 5.3: cost logged from actual billing, never projected). An
    overshoot is logged loudly rather than silently absorbed, because a
    systematic underestimate is how a cap gets exceeded while every
    individual check passes.
    """
    row = session.get(BudgetReservation, reservation_id)
    if row is None:
        raise ValueError(f"no reservation {reservation_id}")
    if actual_usd < 0:
        raise ValueError("a negative actual cost is not meaningful")
    if row.status == "settled":
        logger.info("reservation %s already settled; leaving it alone",
                    reservation_id)
        return
    if row.status == "released":
        raise ValueError(
            f"reservation {reservation_id} was released as never-billed; "
            f"settling it now would contradict that record")

    row.actual_usd = float(actual_usd)
    row.status = "settled"
    row.settled_at = datetime.now(timezone.utc)
    if run_id is not None:
        row.run_id = run_id
    if notes:
        row.notes = ((row.notes or "") + " | " + notes).strip(" |")
    session.flush()

    if actual_usd > row.estimated_usd * 1.5 and actual_usd > 0.01:
        logger.warning(
            "provider %s billed $%.4f against a $%.4f estimate for %s. A "
            "systematic underestimate is how a cap is exceeded while every "
            "check passes - revisit the estimate",
            row.provider, actual_usd, row.estimated_usd, row.idempotency_key)


def release(session: Session, reservation_id: int, reason: str) -> None:
    """Give budget back, for a call that definitely did not bill.

    Only safe when the caller knows no money left - a refusal before the
    request was sent, or a transport error with no response. Anything
    ambiguous must be left to expire instead, because releasing a charge
    that did happen is how the cap silently stops meaning anything.
    """
    row = session.get(BudgetReservation, reservation_id)
    if row is None:
        raise ValueError(f"no reservation {reservation_id}")
    if row.status == "settled":
        raise ValueError(
            f"reservation {reservation_id} is settled at "
            f"${row.actual_usd:.4f}; it cannot be released")
    row.status = "released"
    row.settled_at = datetime.now(timezone.utc)
    row.notes = ((row.notes or "") + f" | released: {reason}").strip(" |")
    session.flush()
    logger.info("released $%.4f of %s budget (%s): %s", row.estimated_usd,
                row.provider, row.idempotency_key, reason)


def budget_state(session: Session, *, provider: str | None = None,
                 now: datetime | None = None) -> dict:
    """What the UI and the logs need, for one provider or all authorised."""
    cfg = config.settings().spending
    period = period_of(now)
    providers = ([provider] if provider
                 else list(cfg.providers_authorised) or [])
    per: dict[str, dict] = {}
    total = 0.0
    for name in providers:
        state = committed_usd(session, name, period)
        per[name] = state
        total += state["committed_usd"]
    return {
        "period": period,
        "monthly_cap_usd": cfg.monthly_cap_usd,
        "providers_authorised": list(cfg.providers_authorised),
        "any_authorised": cfg.any_authorised,
        "committed_usd": round(total, 4),
        "available_usd": round(max(0.0, cfg.monthly_cap_usd - total), 4),
        "by_provider": per,
        #: True when nothing may be spent at all, which the interface shows
        #: instead of offering a button that cannot work.
        "spending_blocked": not cfg.any_authorised,
    }
