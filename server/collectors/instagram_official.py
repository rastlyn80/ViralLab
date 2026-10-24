"""Instagram collector — Facebook Login path (graph.facebook.com).

Path choice is ADR 0001: Hashtag Search and Business Discovery exist only
here, and they are the project's sole official foothold beyond its own
account. Token mechanics are ADR 0005 — the durable credential is a
long-lived **Page** token, which does not expire, so there is no
`ig_refresh_token` call in this module (that belongs to the Instagram Login
path, which this project does not use).

Metric names reflect Meta's April 2025 change: `impressions` and `plays` are
gone, replaced by `views`. `impressions` survives only for media created
before 2 July 2024, so it is collected as historical context and must never
be used as a model feature.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterator

import httpx

from server.collectors.base import (
    DataSourceAdapter, NormalizedMetrics, NormalizedVideo,
)
from server.collectors.tokens import ReauthRequired, TokenStore

logger = logging.getLogger("virallab.collectors.instagram")

GRAPH = "https://graph.facebook.com"
DEFAULT_VERSION = "v22.0"

MEDIA_FIELDS = [
    "id", "caption", "media_type", "media_product_type", "media_url",
    "permalink", "thumbnail_url", "timestamp", "like_count", "comments_count",
]
#: Post-April-2025 metric set (ADR 0001). `plays`/`impressions` are retired.
#: `likes` and `comments` ARE valid reel insight metrics and are requested
#: here — an earlier version mapped them from the response but never asked
#: for them, so both were silently always None.
REEL_INSIGHT_METRICS = ["views", "reach", "saved", "shares", "likes",
                        "comments", "total_interactions"]

#: Also available on reels for the account owner, and directly relevant to
#: the retention questions in plan section 3:
#: `ig_reels_avg_watch_time`, `ig_reels_video_view_total_time`,
#: `reels_skip_rate`. Not requested yet — adding them is a Phase 3 decision,
#: not a collector fix.

#: Meta's 190-series errors mean the credential is invalid, not busy:
#: retrying cannot fix it.
REAUTH_ERROR_CODES = {190, 102, 463, 467}


class InstagramAuth:
    """Long-lived token derivation. Tokens are never logged."""

    def __init__(self, app_id: str, app_secret: str,
                 version: str = DEFAULT_VERSION,
                 client: httpx.Client | None = None):
        self.app_id = app_id
        self.app_secret = app_secret
        self.version = version
        self.http = client or httpx.Client(timeout=30.0)

    def exchange_for_long_lived_user_token(self, short_token: str) -> dict:
        """Short-lived User token -> long-lived User token (60 days)."""
        r = self.http.get(f"{GRAPH}/{self.version}/oauth/access_token", params={
            "grant_type": "fb_exchange_token",
            "client_id": self.app_id,
            "client_secret": self.app_secret,
            "fb_exchange_token": short_token,
        })
        if r.status_code >= 400:
            logger.error("fb token exchange returned %s", r.status_code)
            raise ReauthRequired(
                f"token exchange failed with HTTP {r.status_code}")
        return r.json()

    def list_pages(self, long_lived_user_token: str) -> list[dict]:
        """Pages the user administers, each with its own Page token.

        Page tokens derived from a long-lived User token do not expire, which
        is why this project stores a Page token rather than refreshing.
        """
        r = self.http.get(f"{GRAPH}/{self.version}/me/accounts", params={
            "access_token": long_lived_user_token,
            "fields": "id,name,access_token,instagram_business_account{id,username}",
        })
        r.raise_for_status()
        return r.json().get("data", [])


class InstagramOfficialAdapter(DataSourceAdapter):
    platform = "instagram"
    official = True

    def __init__(self, store: TokenStore, version: str = DEFAULT_VERSION,
                 client: httpx.Client | None = None):
        self.store = store
        self.version = version
        self.http = client or httpx.Client(timeout=60.0)

    def _token(self, account_ref: str) -> str:
        # No refresher: the Page token does not expire. If it stops working,
        # that is invalidation and needs interactive re-authorisation.
        return self.store.access_token(self.platform, account_ref)

    def _get(self, account_ref: str, path: str, **params) -> dict:
        # Bearer header, NOT ?access_token= . Meta accepts both, but a token
        # in a query string can end up in proxy logs, browser history and
        # Referer headers; a header does not.
        headers = {"Authorization": f"Bearer {self._token(account_ref)}"}
        r = self.http.get(f"{GRAPH}/{self.version}/{path.lstrip('/')}",
                          params=params, headers=headers)
        payload = r.json() if r.content else {}
        err = payload.get("error") or {}
        if err:
            code = err.get("code")
            msg = err.get("message", "")
            if code in REAUTH_ERROR_CODES:
                self.store.mark_reauth_required(
                    self.platform, account_ref, f"Meta error {code}: {msg}")
                raise ReauthRequired(f"Meta error {code}: {msg}")
            raise RuntimeError(f"Meta error {code}: {msg}")
        r.raise_for_status()
        return payload

    def _ig_user_id(self, account_ref: str) -> str:
        row = self.store.get(self.platform, account_ref)
        if row is None:
            raise ReauthRequired(f"no credential for instagram/{account_ref}")
        return row.account_ref

    # -- adapter interface ------------------------------------------------

    def fetch_account_videos(self, account_ref: str, *,
                             since: datetime | None = None
                             ) -> Iterator[NormalizedVideo]:
        ig_id = self._ig_user_id(account_ref)
        path, params = f"{ig_id}/media", {"fields": ",".join(MEDIA_FIELDS),
                                          "limit": 50}
        seen = 0
        # The media edge is *observed* to return newest-first, but Meta does
        # not document that guarantee. Stopping at the first older post would
        # therefore silently drop eligible posts if the order ever varied.
        # Instead: verify the order as we go, and only take the early exit
        # while it has actually held.
        order_holds, previous = True, None
        while path:
            payload = self._get(account_ref, path, **params)
            items = payload.get("data", [])

            # Decide about ordering across the WHOLE page before acting on
            # it: a violation later in the page would otherwise be invisible
            # until after an early exit had already dropped eligible posts.
            stamps = [_iso(i.get("timestamp")) for i in items]
            known = [t for t in stamps if t]
            page_ordered = all(a >= b for a, b in zip(known, known[1:]))
            if previous and known and known[0] > previous:
                page_ordered = False
            if not page_ordered and order_holds:
                logger.warning(
                    "instagram media for %s is not newest-first; disabling "
                    "early stop and scanning every page", account_ref)
            order_holds = order_holds and page_ordered
            if known:
                previous = known[-1]

            for raw, posted in zip(items, stamps):
                if since and posted and posted < since:
                    continue            # never drop the rest of the page
                seen += 1
                caption = raw.get("caption") or ""
                yield NormalizedVideo(
                    external_id=str(raw["id"]), platform=self.platform,
                    caption=caption or None, hashtags=_hashtags(caption),
                    posted_at=posted,
                    duration_s=None,     # not exposed on the media edge
                    media_url=raw.get("media_url"),
                    # Signed CDN link: download on collection or lose it.
                    media_url_expires=bool(raw.get("media_url")),
                    raw=raw,
                )
            # Safe to stop only when this page was ordered, every earlier
            # page was too, and the page's OLDEST item is already past the
            # cutoff - so nothing newer can appear later.
            if (since and order_holds and known and known[-1] < since):
                return

            nxt = (payload.get("paging") or {}).get("next")
            if not nxt:
                break
            # The cursor is what matters; rebuild rather than following the
            # absolute URL, so the access token is never put in a log line.
            after = (payload.get("paging") or {}).get("cursors", {}).get("after")
            if not after:
                break
            params = {"fields": ",".join(MEDIA_FIELDS), "limit": 50,
                      "after": after}
        logger.info("instagram: listed %d media item(s) for %s", seen,
                    account_ref)

    def fetch_metrics(self, account_ref: str, external_ids: list[str],
                      ) -> Iterator[NormalizedMetrics]:
        now = datetime.now(timezone.utc)
        for ext in external_ids:
            try:
                payload = self._get(account_ref, f"{ext}/insights",
                                    metric=",".join(REEL_INSIGHT_METRICS))
            except RuntimeError as exc:
                # Insights are unavailable for some media types; a single
                # unsupported item must not abort the whole collection.
                logger.warning("instagram insights unavailable for %s: %s",
                               ext, str(exc)[:160])
                continue
            vals = _flatten_insights(payload)
            yield NormalizedMetrics(
                external_id=ext, captured_at=now,
                views=vals.get("views"),
                likes=vals.get("likes"),
                comments=vals.get("comments"),
                shares=vals.get("shares"),
                saves=vals.get("saved"),
                is_rounded=False,
                raw=vals,
            )


# ---------------------------------------------------------------------------

def _flatten_insights(payload: dict) -> dict:
    out: dict[str, int] = {}
    for item in payload.get("data", []):
        name = item.get("name")
        values = item.get("values") or []
        if not name or not values:
            continue
        val = values[0].get("value")
        if isinstance(val, int):
            out[name] = val
    return out


def _iso(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _hashtags(text: str) -> list[str]:
    import re
    return [m.lower() for m in re.findall(r"#(\w+)", text or "")]
