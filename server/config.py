"""Typed access to `config/settings.yaml`, and the spending guard.

The spending section existed in configuration from the beginning and was
read by **nothing**. That was recorded as a minor outstanding issue while no
paid provider existed. Phase 4 discovery needs one, so it is now a real
gate: official APIs expose no competitor data, so discovery means paying a
third-party provider, and CLAUDE.md §7 forbids silently introducing a paid
dependency or exceeding a configured limit.

**`server.budget.reserve()` is the door every paid call goes through**,
not `require_spend()` below. That was true when this module was written
and stopped being true when `budget.py` arrived with locking, idempotent
reservations and per-category caps. Verified 2026-10-26: `require_spend`
has no caller outside its own tests.

The distinction matters because the two read different tables.
`budget.reserve()` sums `budget_reservations`, which is where provider
spend actually lands — $0.0810 committed in October 2026.
`spent_this_month()` below sums `collection_runs.cost_usd`, which for the
same period is $0.00. The advisory function is not wrong about what it
measures; it measures something that is no longer where the money is.

Configuration still refuses by default: `monthly_cap_usd` ships as `0.0`
and `providers_authorised` ships empty, so a provider call cannot happen
until the owner has authorised both.
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

#: Operator-authorised spending, kept OUT of the repository and out of the
#: deployment sync.
#:
#: `settings.yaml` is code configuration: it is committed, and `deploy.py`
#: copies `config/` to the server on every deploy. Spending authorisation is
#: not code configuration - it is a decision a person made about money, at
#: runtime, through the dashboard. Keeping it in the committed file meant a
#: deploy overwrote the server's `monthly_cap_usd: 5.0` with the repository's
#: `0.0`, silently de-authorising a provider the owner had approved.
#:
#: The reverse is the dangerous direction and is what this really guards
#: against: a non-zero cap committed to the repository would be pushed to
#: every deployment and silently GRANT spending nobody approved there.
#:
#: This file overlays the `spending` section of `settings.yaml`. It is
#: gitignored, excluded from the deploy sync, and written only by
#: `PUT /api/credentials/spending`.
SPENDING_OVERRIDE_PATH = Path(os.environ.get(
    "VIRALLAB_SPENDING_FILE",
    str(ROOT / "config" / "spending.local.yaml")))


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
    #: Per-category ceilings, each capped by `monthly_cap_usd` as well.
    #: Absent means the category is NOT authorised - not "unlimited". A
    #: missing key must never read as permission.
    category_caps_usd: tuple = ()

    @property
    def any_authorised(self) -> bool:
        return self.monthly_cap_usd > 0 and bool(self.providers_authorised)

    #: The category that existed before categories did. An installation
    #: with `monthly_cap_usd` and `providers_authorised` set has ALREADY
    #: authorised discovery spending, so requiring it to also name a
    #: category cap would silently switch off a working, approved feature.
    #: That is a regression wearing a safety costume.
    LEGACY_CATEGORY = "discovery"

    def cap_for(self, category: str) -> float:
        """The ceiling for one category.

        An explicitly configured cap always wins, and is additionally
        bounded by the global cap.

        Otherwise: `discovery` inherits the global cap, because the
        existing configuration already authorised it. Every category added
        afterwards - cloud vision, cloud audio, strategy generation -
        returns 0.0, because nothing the owner has configured so far says
        anything about paying a model to look at video. An unconfigured
        NEW category means "not permitted", never "unlimited".
        """
        for item in self.category_caps_usd:
            name, value = item
            if name == category:
                return min(float(value), self.monthly_cap_usd)
        if category == self.LEGACY_CATEGORY:
            return self.monthly_cap_usd
        return 0.0


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
    sp = dict(raw.get("spending") or {})

    # Overlay the operator's runtime authorisation, if any. Read after the
    # committed file so a deploy cannot change what was approved on this
    # machine - in either direction.
    override = _load_spending_override()
    if override:
        sp.update(override)
        raw = {**raw, "spending": sp}

    providers = sp.get("providers_authorised") or []
    if isinstance(providers, str):
        providers = [providers]
    return Settings(
        raw=raw,
        spending=Spending(
            monthly_cap_usd=float(sp.get("monthly_cap_usd") or 0.0),
            providers_authorised=tuple(str(x) for x in providers),
            category_caps_usd=_category_caps(sp.get("category_caps_usd"))))


def _load_spending_override() -> dict:
    """The operator's runtime spending authorisation, or {}.

    Deliberately forgiving about absence - no file means "nothing has been
    authorised at runtime, use the committed configuration" - and strict
    about corruption: an unreadable override returns {} and logs loudly
    rather than guessing, because guessing here either blocks approved work
    or permits unapproved charges.
    """
    path = SPENDING_OVERRIDE_PATH
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:                        # noqa: BLE001
        logger.error("spending override at %s could not be read (%s); "
                     "falling back to the committed configuration", path,
                     exc)
        return {}
    if not isinstance(data, dict):
        logger.error("spending override at %s is not a mapping; ignoring",
                     path)
        return {}
    section = data.get("spending", data)
    if not isinstance(section, dict):
        logger.error("spending override at %s has no usable spending "
                     "section; ignoring", path)
        return {}
    return section


def _category_caps(value) -> tuple:
    """Parse per-category ceilings into `((name, usd), ...)`.

    `Spending.cap_for` has always honoured this field; nothing ever filled
    it, so every per-category cap written in `settings.yaml` was silently
    ignored and only the global cap applied. That is the defect this
    function exists to close.

    **Every failure mode resolves to 0.0, never to the global cap.** A
    malformed, negative or non-numeric entry means "not permitted" - the
    same as absent - because the one thing a spending guard must never do
    is read a configuration mistake as authorisation. A cap above the
    global ceiling is not rejected here; `cap_for` already bounds it with
    `min()`, so the global cap stays the hard limit.

    Accepts the natural YAML mapping::

        category_caps_usd:
          discovery: 5.00
          cloud_vision: 2.00

    and a list of pairs, which is what `yaml.safe_dump` produces if the
    value is ever written back as tuples.
    """
    if not value:
        return ()
    items: list[tuple[str, float]] = []

    if isinstance(value, dict):
        pairs = list(value.items())
    elif isinstance(value, (list, tuple)):
        pairs = []
        for entry in value:
            if isinstance(entry, dict) and len(entry) == 1:
                pairs.extend(entry.items())
            elif isinstance(entry, (list, tuple)) and len(entry) == 2:
                pairs.append((entry[0], entry[1]))
            else:
                logger.warning(
                    "spending.category_caps_usd: ignoring malformed entry "
                    "%r; it authorises nothing", entry)
    else:
        logger.warning(
            "spending.category_caps_usd should be a mapping of category to "
            "USD, got %s; no per-category cap is authorised",
            type(value).__name__)
        return ()

    for name, usd in pairs:
        try:
            amount = float(usd)
        except (TypeError, ValueError):
            logger.warning(
                "spending.category_caps_usd[%s] = %r is not a number; "
                "treating the category as NOT authorised", name, usd)
            amount = 0.0
        if amount < 0:
            logger.warning(
                "spending.category_caps_usd[%s] = %s is negative; treating "
                "the category as NOT authorised", name, amount)
            amount = 0.0
        items.append((str(name), amount))
    return tuple(items)


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
    """Total `collection_runs.cost_usd` in the current calendar month.

    **Advisory only, and it does not see discovery spend.** The docstring
    here used to say `collection_runs.cost_usd` "is the only place cost is
    recorded"; that stopped being true when discovery moved to
    `budget_reservations` via `server.budget`. Measured 2026-10-26, the
    two disagree for October: $0.00 here against $0.0810 there.

    Enforcement reads the right one — `budget.reserve()` sums
    `budget_reservations`, and the API's budget endpoints call
    `budget.budget_state`, not this. So nothing user-facing is misreported
    and no cap is under-enforced. This function survives because
    `require_spend` calls it, and `require_spend` survives because its
    tests do.

    Do not build anything new on this. Use `server.budget`.
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

    **This is not the enforcement path, and it has no callers outside its
    own tests** (verified 2026-10-26). It answers "is there room?" subject
    to time-of-check/time-of-use, and it answers it from
    `collection_runs.cost_usd`, which no longer carries discovery spend —
    see `spent_this_month`.

    Anything that actually spends money must call
    `server.budget.reserve()`, which takes a lock, holds the budget until
    settled, is idempotent across retries, enforces per-category caps, and
    reads the table the money is actually recorded in.
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
