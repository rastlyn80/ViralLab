"""OAuth token storage and refresh, per ADR 0005.

The correctness detail that matters most: **TikTok rotates the refresh token
on every refresh.** Persisting only the new access token works for exactly one
cycle and then fails permanently 24 hours later, with nothing in the logs to
explain it. Both values are therefore written together, in one transaction,
under a row lock.

Tokens are never logged — only platform, account reference and HTTP status.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.db.models import OAuthToken, utcnow

logger = logging.getLogger("virallab.tokens")

#: Refresh when less than this remains, rather than waiting for a 401. With
#: TikTok's 24h access token this leaves three more chances before anything
#: actually breaks (ADR 0005 decision 3).
REFRESH_WHEN_UNDER = timedelta(hours=6)


class ReauthRequired(RuntimeError):
    """The account owner must re-authorise interactively.

    Distinct from a transient failure: retrying cannot fix it, so collectors
    defer and surface it rather than looping.
    """


class TransientAuthError(RuntimeError):
    """The auth endpoint was unreachable or returned a server error.

    Retrying CAN fix this, so it must never be mistaken for ReauthRequired —
    flagging a healthy credential as needing re-authorisation would demand a
    pointless manual re-consent and stop collection until someone noticed.
    """


class TokenStore:
    def __init__(self, session: Session):
        self.session = session

    def get(self, platform: str, account_ref: str) -> OAuthToken | None:
        return self.session.scalar(select(OAuthToken).where(
            OAuthToken.platform == platform,
            OAuthToken.account_ref == account_ref))

    def _locked(self, platform: str, account_ref: str) -> OAuthToken | None:
        return self.session.scalar(select(OAuthToken).where(
            OAuthToken.platform == platform,
            OAuthToken.account_ref == account_ref).with_for_update())

    def upsert(self, *, platform: str, account_ref: str, access_token: str,
               refresh_token: str | None = None,
               expires_in: int | None = None,
               refresh_expires_in: int | None = None,
               scope: str | None = None, page_id: str | None = None,
               ) -> OAuthToken:
        """Create or replace a credential. `expires_in=None` means the token
        does not expire — true of Instagram Page tokens (ADR 0005)."""
        row = self._locked(platform, account_ref)
        now = utcnow()
        if row is None:
            row = OAuthToken(platform=platform, account_ref=account_ref,
                             access_token=access_token)
            self.session.add(row)
        row.access_token = access_token
        if refresh_token is not None:
            row.refresh_token = refresh_token
        row.expires_at = (now + timedelta(seconds=expires_in)
                          if expires_in else None)
        if refresh_expires_in:
            row.refresh_expires_at = now + timedelta(seconds=refresh_expires_in)
        if scope is not None:
            row.scope = scope
        if page_id is not None:
            row.page_id = page_id
        row.needs_reauth = False
        row.last_error = None
        row.last_refreshed_at = now
        self.session.flush()
        logger.info("stored %s credential for %s (expires_at=%s)",
                    platform, account_ref, row.expires_at)
        return row

    def mark_reauth_required(self, platform: str, account_ref: str,
                             reason: str) -> None:
        row = self._locked(platform, account_ref)
        if row is None:
            return
        row.needs_reauth = True
        row.last_error = reason[:2000]
        self.session.flush()
        logger.error("re-authorisation required for %s/%s: %s",
                     platform, account_ref, reason[:200])

    def needs_refresh(self, row: OAuthToken, *,
                      now: datetime | None = None) -> bool:
        if row.expires_at is None:        # non-expiring (IG Page token)
            return False
        return row.expires_at - (now or utcnow()) < REFRESH_WHEN_UNDER

    def access_token(self, platform: str, account_ref: str, *,
                     refresher=None) -> str:
        """Return a usable access token, refreshing first if it is close to
        expiry. Raises ReauthRequired when no automated recovery exists.

        The row is locked BEFORE the refresh decision and re-checked under the
        lock. Without that, two concurrent callers both read the stale token
        and both refresh; since TikTok rotates, the second call carries an
        already-invalidated refresh token and its response can overwrite the
        newer one — permanently breaking the credential. With the lock, the
        second caller blocks, then re-reads and finds the token already fresh.
        """
        row = self._locked(platform, account_ref)
        if row is None:
            raise ReauthRequired(
                f"no stored credential for {platform}/{account_ref}")
        if row.needs_reauth:
            raise ReauthRequired(
                f"{platform}/{account_ref} needs re-authorisation: "
                f"{row.last_error}")
        if self.needs_refresh(row):
            if refresher is None:
                raise ReauthRequired(
                    f"{platform}/{account_ref} token expires at "
                    f"{row.expires_at} and no refresher was supplied")
            if (row.refresh_expires_at
                    and row.refresh_expires_at <= utcnow()):
                self.mark_reauth_required(
                    platform, account_ref,
                    "refresh token itself expired; interactive "
                    "re-authorisation required")
                raise ReauthRequired(
                    f"{platform}/{account_ref} refresh token expired")
            # Re-check under the lock: another caller may have refreshed
            # while this one waited, in which case there is nothing to do.
            if self.needs_refresh(row):
                refresher(self, row)
            row = self.get(platform, account_ref)
        return row.access_token


def apply_tiktok_refresh(store: TokenStore, row: OAuthToken,
                         payload: dict,
                         used_refresh_token: str | None = None) -> OAuthToken:
    """Persist a TikTok refresh response.

    TikTok MAY return a different refresh_token and the docs require using the
    new one. Writing both together is what keeps the credential alive beyond
    a single cycle.
    """
    # Guard against a stale response. `used_refresh_token` is the value the
    # caller actually sent; comparing two ORM reads would not work, because
    # SQLAlchemy's identity map hands back the same object and therefore the
    # same (newest) value.
    current = store.get(row.platform, row.account_ref)
    if (used_refresh_token and current is not None
            and current.refresh_token
            and current.refresh_token != used_refresh_token):
        logger.warning(
            "discarding stale tiktok refresh response for %s: the stored "
            "refresh token already rotated", row.account_ref)
        return current

    previous = used_refresh_token or row.refresh_token
    new_refresh = payload.get("refresh_token") or previous
    rotated = bool(payload.get("refresh_token")
                   and payload["refresh_token"] != previous)
    out = store.upsert(
        platform=row.platform, account_ref=row.account_ref,
        access_token=payload["access_token"],
        refresh_token=new_refresh,
        expires_in=payload.get("expires_in"),
        refresh_expires_in=payload.get("refresh_expires_in"),
        scope=payload.get("scope"),
    )
    logger.info("refreshed tiktok credential for %s (refresh token %s)",
                row.account_ref, "ROTATED" if rotated else "unchanged")
    return out
