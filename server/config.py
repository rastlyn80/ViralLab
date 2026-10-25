"""Typed access to `config/settings.yaml`, and the spending guard.

The spending section existed in configuration from the beginning and was
read by **nothing**. That was recorded as a minor outstanding issue while no
paid provider existed. Phase 4 discovery needs one, so it is now a real
gate: official APIs expose no competitor data, so discovery means paying a
third-party provider, and CLAUDE.md §7 forbids silently introducing a paid
dependency or exceeding a configured limit.

`require_spend()` is the single door every paid call goes through. It
refuses by default: `monthly_cap_usd` ships as `0.0` and
`providers_authorised` ships empty, so a provider call cannot happen until
the owner has authorised both.

    from server.config import require_spend, SpendRefused

    try:
        require_spend(session, provider="apify", estimated_usd=0.30)
    except SpendRefused as exc:
        # Not an error to retry. A human has to authorise it.
        logger.warning("discovery skipped: %s", exc)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

logger = logging.getLogger("virallab.config")

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = Path(os.environ.get(
    "VIRALLAB_SETTINGS", str(ROOT / "config" / "settings.yaml")))


class SpendRefused(RuntimeError):
    """A paid call was refused. Not transient - a human must authorise it.

    Distinct from a rate limit or a network error on purpose: retrying this
    cannot help, and treating it as transient would turn a budget refusal
    into a retry loop.
    """


@dataclass(frozen=True)
class Spending:
    monthly_cap_usd: float = 0.0
    providers_authorised: tuple[str, ...] = ()

    @property
    def any_authorised(self) -> bool:
        return self.monthly_cap_usd > 0 and bool(self.providers_authorised)


@dataclass(frozen=True)
class Settings:
    raw: dict = field(default_factory=dict)
    spending: Spending = field(default_factory=Spending)

    def section(self, name: str) -> dict:
        return dict(self.raw.get(name) or {})


def load_settings(path: Path | None = None) -> Settings:
    p = path or SETTINGS_PATH
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        logger.warning("no settings file at %s; defaults apply and no "
                       "spending is authorised", p)
        raw = {}
    sp = raw.get("spending") or {}
    providers = sp.get("providers_authorised") or []
    if isinstance(providers, str):
        providers = [providers]
    return Settings(
        raw=raw,
        spending=Spending(
            monthly_cap_usd=float(sp.get("monthly_cap_usd") or 0.0),
            providers_authorised=tuple(str(x) for x in providers)))


@lru_cache(maxsize=1)
def settings() -> Settings:
    return load_settings()


def reload_settings() -> Settings:
    settings.cache_clear()
    return settings()


def month_start(now: datetime | None = None) -> datetime:
    n = now or datetime.now(timezone.utc)
    return n.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def spent_this_month(session: Session, *, now: datetime | None = None
                     ) -> float:
    """Total recorded provider spend in the current calendar month.

    Reads `collection_runs.cost_usd`, which is the only place cost is
    recorded. A provider call that does not record its cost is a bug, so an
    unrecorded call makes the budget look emptier than it is - hence
    `record_spend` below exists and is the only sanctioned way to log one.
    """
    from server.db.models import CollectionRun
    total = session.scalar(
        select(func.coalesce(func.sum(CollectionRun.cost_usd), 0.0))
        .where(CollectionRun.started_at >= month_start(now))) or 0.0
    return float(total)


def budget_state(session: Session, *, now: datetime | None = None) -> dict:
    s = settings().spending
    spent = spent_this_month(session, now=now)
    return {
        "monthly_cap_usd": s.monthly_cap_usd,
        "providers_authorised": list(s.providers_authorised),
        "spent_this_month_usd": round(spent, 4),
        "remaining_usd": round(max(0.0, s.monthly_cap_usd - spent), 4),
        "any_authorised": s.any_authorised,
        "month_started": month_start(now).isoformat(),
    }


def require_spend(session: Session, *, provider: str, estimated_usd: float,
                  now: datetime | None = None) -> dict:
    """Check whether a paid call *would* be allowed. Reserves nothing.

    **This is not the enforcement path.** It answers "is there room?" for a
    report or a UI, and it is subject to time-of-check/time-of-use: two
    callers can both pass it and together exceed the cap.

    Anything that actually spends money must call
    `server.budget.reserve()`, which takes a lock, holds the budget until
    settled, and is idempotent across retries.
    """
    s = settings().spending
    if provider not in s.providers_authorised:
        raise SpendRefused(
            f"provider {provider!r} is not in providers_authorised "
            f"{list(s.providers_authorised)}; authorise it in "
            f"config/settings.yaml before any paid call")
    if s.monthly_cap_usd <= 0:
        raise SpendRefused(
            "spending.monthly_cap_usd is 0.00, so no paid service is "
            "authorised")
    if estimated_usd < 0:
        raise SpendRefused("a negative estimated cost is not meaningful")

    spent = spent_this_month(session, now=now)
    projected = spent + estimated_usd
    if projected > s.monthly_cap_usd:
        raise SpendRefused(
            f"this call would bring {month_start(now):%Y-%m} spend to "
            f"${projected:.4f}, over the ${s.monthly_cap_usd:.2f} cap "
            f"(${spent:.4f} already spent). Overage requires explicit "
            f"approval, never an automatic charge")
    return {"provider": provider, "estimated_usd": estimated_usd,
            "spent_before_usd": round(spent, 4),
            "projected_usd": round(projected, 4),
            "cap_usd": s.monthly_cap_usd}


def record_spend(session: Session, run, actual_usd: float) -> None:
    """Attach an actual cost to a collection run.

    Separate from `require_spend` because the estimate and the charge are
    different numbers, and the plan requires cost to be **logged from actual
    billing** rather than projected (CLAUDE.md §5.3).
    """
    run.cost_usd = float(actual_usd)
    session.flush()
