"""Paste-in credential management for the dashboard.

The owner wanted to stop editing `.env` over SSH and enter platform keys in
the interface instead. That is reasonable, and it is also the most
security-sensitive endpoint in the project, so the constraints are explicit.

**Write-only.** No endpoint here ever returns a credential value. `GET`
returns whether a slot is set, an 8-character SHA-256 fingerprint, and when
it changed. A fingerprint is enough to confirm "the key I pasted is the key
that is stored" by comparing it against one computed locally; it does not
reveal the secret.

**A fixed slot registry, not a key-value store.** `PUT` accepts only the
names in `SLOTS`. Without that, this would be an arbitrary
environment-variable write primitive reachable over HTTP - `PATH`,
`LD_PRELOAD`, `VIRALLAB_DB_URL` and `PYTHONPATH` are all env vars, and any
of them would turn a credential form into remote code execution. The
whitelist is the control that makes the rest of this file acceptable.

**Never logged.** Values are not written to the log at any level, not
included in exception messages, and not echoed in responses. Log lines carry
the slot name and the fingerprint only.

**Authorising spending is a separate action from storing a key.** A token on
its own does nothing: `research_api.active_provider()` additionally requires
the provider to be named in `settings.yaml` and the monthly cap to exceed
zero. Both are deliberate, and pasting a token does not quietly satisfy
either. `PUT /api/credentials/spending` is the explicit, separately-confirmed
action that authorises money, and it is the only thing in this file that can.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from server import config
from server.api.ui import require_ui, require_ui_write

logger = logging.getLogger("virallab.api.credentials")

router = APIRouter(prefix="/api/credentials", tags=["credentials"])

#: The server-side `.env`, the same file the deploy script writes.
ENV_PATH = Path(os.environ.get(
    "VIRALLAB_ENV_PATH",
    Path(__file__).resolve().parents[2] / ".env"))

#: Keys must look like env var names. Belt and braces alongside the
#: whitelist: a registry entry is the only way in, and this stops a
#: malformed registry entry producing a malformed `.env`.
KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")

#: Values may not contain a newline - that would let one credential inject a
#: second `.env` line and set an unrelated variable.
FORBIDDEN_IN_VALUE = ("\n", "\r", "\x00")

MAX_VALUE_BYTES = 8192


@dataclass(frozen=True)
class CredentialSlot:
    key: str
    platform: str
    label: str
    #: What having this unlocks, in the owner's terms.
    purpose: str
    #: What stays broken while it is missing.
    blocked_without: str
    #: Where the owner gets it.
    where: str = ""
    #: False for things that are identifiers rather than secrets; they are
    #: still not returned, because the distinction is not worth the risk of
    #: getting it wrong for one field.
    secret: bool = True
    #: True when the credential costs money to use.
    paid: bool = False


SLOTS: tuple[CredentialSlot, ...] = (
    CredentialSlot(
        key="META_APP_ID", platform="instagram", label="Meta app ID",
        purpose="Identifies the Facebook app used for Instagram Business "
                "Discovery and hashtag search.",
        blocked_without="Instagram collection and competitor discovery "
                        "cannot authenticate.",
        where="developers.facebook.com → your app → Settings → Basic",
        secret=False),
    CredentialSlot(
        key="META_APP_SECRET", platform="instagram", label="Meta app secret",
        purpose="Signs Instagram token exchanges and refreshes.",
        blocked_without="Instagram tokens cannot be refreshed; collection "
                        "stops when the current token expires.",
        where="developers.facebook.com → your app → Settings → Basic"),
    CredentialSlot(
        key="TIKTOK_CLIENT_KEY", platform="tiktok", label="TikTok client key",
        purpose="Identifies the TikTok app for Display API access.",
        blocked_without="TikTok metrics collection and own-account media "
                        "archival cannot authenticate.",
        where="developers.tiktok.com → your app → Credentials",
        secret=False),
    CredentialSlot(
        key="TIKTOK_CLIENT_SECRET", platform="tiktok",
        label="TikTok client secret",
        purpose="Signs TikTok token exchanges and refreshes.",
        blocked_without="TikTok tokens cannot be refreshed; collection stops "
                        "at the next refresh cycle.",
        where="developers.tiktok.com → your app → Credentials"),
    CredentialSlot(
        key="APIFY_TOKEN", platform="tiktok", label="Apify API token",
        purpose="Third-party provider for TikTok competitor research. "
                "TikTok's own Research API is off-limits to this project "
                "(academic/non-profit eligibility only, and it prohibits "
                "commercial use), so competitor observation on TikTok needs "
                "a commercial provider.",
        blocked_without="TikTok competitor research stays in fixture mode "
                        "and returns demonstration data only.",
        where="console.apify.com → Settings → Integrations → API tokens",
        paid=True),
    CredentialSlot(
        key="OPENAI_API_KEY", platform="ai", label="OpenAI API key",
        purpose="Optional cloud vision provider, as an alternative to the "
                "local model on the Windows workstation.",
        blocked_without="Nothing. The local model handles visual analysis "
                        "at zero cost; this is only for comparison.",
        where="platform.openai.com → API keys", paid=True),
    CredentialSlot(
        key="GEMINI_API_KEY", platform="ai", label="Google Gemini API key",
        purpose="Optional cloud vision provider. The only one that accepts "
                "video natively rather than sampled frames.",
        blocked_without="Nothing. Local analysis is unaffected.",
        where="aistudio.google.com → Get API key", paid=True),
    CredentialSlot(
        key="ANTHROPIC_API_KEY", platform="ai", label="Anthropic API key",
        purpose="Optional cloud vision provider.",
        blocked_without="Nothing. Local analysis is unaffected.",
        where="console.anthropic.com → API keys", paid=True),
)

BY_KEY = {s.key: s for s in SLOTS}


class CredentialStatus(BaseModel):
    key: str
    platform: str
    label: str
    purpose: str
    blocked_without: str
    where: str = ""
    secret: bool = True
    paid: bool = False
    is_set: bool = False
    #: First 8 hex characters of SHA-256 of the value. Enough to confirm
    #: which key is stored, not enough to reconstruct it.
    fingerprint: str | None = None
    source: str | None = None


class SpendingStatus(BaseModel):
    monthly_cap_usd: float = 0.0
    providers_authorised: list[str] = Field(default_factory=list)
    #: Effective ceiling per category, after the global cap is applied.
    category_caps_usd: dict[str, float] = Field(default_factory=dict)
    any_authorised: bool = False
    #: Which features are live right now, and why not when they are not.
    research_provider: str = "fixture"
    research_reason: str = ""


class CredentialsView(BaseModel):
    credentials: list[CredentialStatus] = Field(default_factory=list)
    spending: SpendingStatus
    env_path_writable: bool = True
    note: str = ""


class CredentialWrite(BaseModel):
    value: str


class SpendingWrite(BaseModel):
    """Authorising money. Both fields are required, deliberately."""

    monthly_cap_usd: float
    providers_authorised: list[str] = Field(default_factory=list)
    #: Optional per-category ceilings. Omitted leaves existing caps alone;
    #: an explicit {} clears them. A category with no cap is NOT authorised,
    #: except `discovery`, which inherits the global cap for backward
    #: compatibility - see Spending.cap_for.
    category_caps_usd: dict[str, float] | None = None
    #: Must be true. A cap cannot be raised by a request that did not mean to.
    confirm: bool = False


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def _read_env_file() -> dict[str, str]:
    out: dict[str, str] = {}
    if not ENV_PATH.exists():
        return out
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def _write_env_value(key: str, value: str | None) -> None:
    """Set or remove one key in `.env`, atomically, preserving everything else.

    Rewrites through a temporary file in the same directory and replaces, so
    an interrupted write cannot leave a truncated `.env` - which would take
    out database access and collection along with the credential.
    """
    if key not in BY_KEY:
        raise HTTPException(400, f"{key} is not a managed credential")
    if not KEY_RE.match(key):
        raise HTTPException(400, "malformed credential name")

    lines = (ENV_PATH.read_text(encoding="utf-8").splitlines()
             if ENV_PATH.exists() else [])
    replaced = False
    out: list[str] = []
    for line in lines:
        name = line.partition("=")[0].strip()
        if name == key and "=" in line and not line.lstrip().startswith("#"):
            if value is not None:
                out.append(f"{key}={value}")
            replaced = True
            continue
        out.append(line)
    if value is not None and not replaced:
        out.append(f"{key}={value}")

    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(ENV_PATH.parent),
                                    prefix=".env.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        os.write(fd, ("\n".join(out).rstrip("\n") + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(tmp, 0o600)
    tmp.replace(ENV_PATH)


@router.get("", response_model=CredentialsView,
            dependencies=[Depends(require_ui)])
def list_credentials() -> CredentialsView:
    """Which credentials are present. Never their values."""
    from server.api.research_api import APIFY_TOKEN_ENV, active_provider

    file_env = _read_env_file()
    rows: list[CredentialStatus] = []
    for slot in SLOTS:
        # os.environ wins, because that is what the running process uses.
        value = os.environ.get(slot.key) or file_env.get(slot.key) or ""
        source = None
        if value:
            source = ("environment" if os.environ.get(slot.key)
                      else "env file")
        rows.append(CredentialStatus(
            key=slot.key, platform=slot.platform, label=slot.label,
            purpose=slot.purpose, blocked_without=slot.blocked_without,
            where=slot.where, secret=slot.secret, paid=slot.paid,
            is_set=bool(value),
            fingerprint=fingerprint(value) if value else None,
            source=source))

    sp = config.settings().spending
    provider = active_provider()
    if provider != "fixture":
        reason = f"{provider} is authorised and its credential is present"
    elif not sp.providers_authorised:
        reason = ("no provider is authorised in settings, so research "
                  "returns demonstration data")
    elif sp.monthly_cap_usd <= 0:
        reason = ("a provider is authorised but the monthly cap is $0, so "
                  "nothing may be spent")
    elif not os.environ.get(APIFY_TOKEN_ENV):
        reason = ("apify is authorised and funded but its token is not set "
                  "in the running process")
    else:
        reason = "unknown"

    writable = os.access(ENV_PATH.parent, os.W_OK)
    return CredentialsView(
        credentials=rows,
        spending=SpendingStatus(
            monthly_cap_usd=sp.monthly_cap_usd,
            providers_authorised=list(sp.providers_authorised),
            # Every category the project can spend against, so the
            # dashboard shows a $0.00 one rather than omitting it. An
            # absent category reads as "not applicable"; a $0.00 one
            # reads as "not authorised", and only the second is true.
            category_caps_usd={c: sp.cap_for(c) for c in SPEND_CATEGORIES},
            any_authorised=sp.any_authorised,
            research_provider=provider, research_reason=reason),
        env_path_writable=writable,
        note=("Values are never returned by this API. The fingerprint is the "
              "first 8 characters of the SHA-256 of the stored value, which "
              "is enough to check that what you pasted is what is stored."))


#: Categories the spending guard knows about. `competitor_media` was
#: added for M2: downloading a competitor's video through the provider's
#: paid add-on is a different decision from paying for metadata, and
#: giving it its own ceiling stops one starving the other.
SPEND_CATEGORIES = ("discovery", "competitor_media", "cloud_vision",
                    "cloud_audio", "strategy")


@router.put("/spending", response_model=SpendingStatus,
            dependencies=[Depends(require_ui_write)])
def set_spending(body: SpendingWrite) -> SpendingStatus:
    """Authorise paid providers and a monthly ceiling.

    Separate from storing a token, and requires `confirm`, because this is
    the action that permits money to be spent. CLAUDE.md 7 forbids a paid
    dependency appearing without explicit approval, and a token pasted into
    a form is not approval of a budget.
    """
    if not body.confirm:
        raise HTTPException(
            400, "set confirm=true to authorise spending; this is the "
                 "request that permits real charges")
    if body.monthly_cap_usd < 0:
        raise HTTPException(400, "a negative cap is not meaningful")
    if body.monthly_cap_usd > 1000:
        raise HTTPException(
            400, f"${body.monthly_cap_usd:.2f} is far above anything this "
                 f"project needs; refusing in case of a typo. Raise the "
                 f"limit in settings.yaml by hand if it is genuinely "
                 f"intended.")
    unknown = [p for p in body.providers_authorised
               if p not in {"apify", "openai", "gemini", "anthropic"}]
    if unknown:
        raise HTTPException(400, f"unknown provider(s): {', '.join(unknown)}")

    # Written to the runtime override, NOT to the committed settings file.
    # `deploy.py` syncs `config/`, so writing authorisation into
    # settings.yaml meant the next deploy pushed the repository's 0.0 over
    # the operator's approved cap - and, worse, would push a committed
    # non-zero cap onto a machine where nobody had approved it.
    path = config.SPENDING_OVERRIDE_PATH
    before = dict(config.settings().spending.__dict__)
    raw = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raw = {}
    raw.setdefault("spending", {})
    raw["spending"]["monthly_cap_usd"] = float(body.monthly_cap_usd)
    raw["spending"]["providers_authorised"] = list(body.providers_authorised)
    if body.category_caps_usd is not None:
        raw["spending"]["category_caps_usd"] = {
            str(k): float(v) for k, v in body.category_caps_usd.items()}

    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".settings.",
                                    suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        os.write(fd, yaml.safe_dump(raw, sort_keys=False).encode("utf-8"))
    finally:
        os.close(fd)
    tmp.replace(path)
    config.reload_settings()

    logger.warning("SPENDING AUTHORISATION CHANGED by web UI: cap "
                   "$%.2f -> $%.2f, providers %s -> %s (written to %s)",
                   float(before.get("monthly_cap_usd") or 0.0),
                   body.monthly_cap_usd,
                   before.get("providers_authorised") or [],
                   body.providers_authorised, path.name)

    from server.api.research_api import active_provider
    sp = config.settings().spending
    return SpendingStatus(
        monthly_cap_usd=sp.monthly_cap_usd,
        providers_authorised=list(sp.providers_authorised),
        category_caps_usd={c: sp.cap_for(c) for c in ("discovery", "cloud_vision", "cloud_audio", "strategy")},
        any_authorised=sp.any_authorised,
        research_provider=active_provider(),
        research_reason="updated from the dashboard")


@router.put("/{key}", response_model=CredentialStatus,
            dependencies=[Depends(require_ui_write)])
def set_credential(key: str, body: CredentialWrite) -> CredentialStatus:
    """Store one credential. The value is never logged or returned."""
    slot = BY_KEY.get(key)
    if slot is None:
        raise HTTPException(
            404, f"{key} is not a managed credential. Managed: "
                 f"{', '.join(sorted(BY_KEY))}")
    value = (body.value or "").strip()
    if not value:
        raise HTTPException(400, "empty value; use DELETE to clear a slot")
    if len(value.encode("utf-8")) > MAX_VALUE_BYTES:
        raise HTTPException(400, "value is implausibly long; refusing")
    for bad in FORBIDDEN_IN_VALUE:
        if bad in value:
            raise HTTPException(
                400, "value contains a line break or control character, "
                     "which could inject an unrelated setting")

    _write_env_value(key, value)
    # Make it effective now. Without this the owner would paste a token and
    # see no change until the service restarted, and would reasonably
    # conclude it had not worked.
    os.environ[key] = value

    fp = fingerprint(value)
    logger.info("credential %s set via web UI (fingerprint %s)", key, fp)
    return CredentialStatus(
        key=slot.key, platform=slot.platform, label=slot.label,
        purpose=slot.purpose, blocked_without=slot.blocked_without,
        where=slot.where, secret=slot.secret, paid=slot.paid,
        is_set=True, fingerprint=fp, source="environment")


@router.delete("/{key}", response_model=CredentialStatus,
               dependencies=[Depends(require_ui_write)])
def clear_credential(key: str) -> CredentialStatus:
    slot = BY_KEY.get(key)
    if slot is None:
        raise HTTPException(404, f"{key} is not a managed credential")
    _write_env_value(key, None)
    os.environ.pop(key, None)
    logger.info("credential %s cleared via web UI", key)
    return CredentialStatus(
        key=slot.key, platform=slot.platform, label=slot.label,
        purpose=slot.purpose, blocked_without=slot.blocked_without,
        where=slot.where, secret=slot.secret, paid=slot.paid, is_set=False)
