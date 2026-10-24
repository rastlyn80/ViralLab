"""TikTok Display API collector (own account only).

Endpoints and token mechanics verified against TikTok's own documentation in
ADR 0005 and ADR 0001. Two facts shape this module:

  * The Display API returns NO downloadable media — only `embed_link` and
    `share_url`. Own TikTok media comes from the local master archive, so
    `media_url` is always None here.
  * The refresh token ROTATES. `apply_tiktok_refresh` writes both tokens
    together; anything less works for one cycle then fails permanently.

The Research API is deliberately absent: it is restricted to non-commercial
academic use and is out of scope under any business model (ADR 0001).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterator

import httpx

from server.collectors.base import (
    DataSourceAdapter, NormalizedMetrics, NormalizedVideo,
)
from server.collectors.tokens import ReauthRequired, TokenStore, apply_tiktok_refresh

logger = logging.getLogger("virallab.collectors.tiktok")

API = "https://open.tiktokapis.com/v2"
TOKEN_URL = f"{API}/oauth/token/"
AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"

#: Exactly the fields TikTok documents on the Video Object. `collect_count`
#: (saves) is NOT among them — Revision 1 of the plan wrongly claimed it was.
VIDEO_FIELDS = [
    "id", "create_time", "cover_image_url", "share_url", "video_description",
    "duration", "height", "width", "title", "embed_link", "like_count",
    "comment_count", "share_count", "view_count", "is_aigc",
]

SCOPES = ["user.info.basic", "user.info.stats", "video.list"]


def authorize_url(client_key: str, redirect_uri: str, state: str,
                  scopes: list[str] | None = None) -> str:
    """Build the interactive consent URL the account owner must visit once."""
    from urllib.parse import urlencode
    return AUTHORIZE_URL + "?" + urlencode({
        "client_key": client_key,
        "scope": ",".join(scopes or SCOPES),
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
    })


class TikTokAuth:
    """Token exchange and refresh. Tokens are never logged."""

    def __init__(self, client_key: str, client_secret: str,
                 client: httpx.Client | None = None):
        self.client_key = client_key
        self.client_secret = client_secret
        self.http = client or httpx.Client(timeout=30.0)

    def _post(self, data: dict) -> dict:
        r = self.http.post(
            TOKEN_URL, data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        if r.status_code >= 400:
            # Log the status, never the body: it can echo the secret back.
            logger.error("tiktok token endpoint returned %s", r.status_code)
            raise ReauthRequired(
                f"tiktok token request failed with HTTP {r.status_code}")
        payload = r.json()
        if "error" in payload and payload.get("error"):
            raise ReauthRequired(
                f"tiktok token error: {payload.get('error')}")
        return payload

    def exchange_code(self, code: str, redirect_uri: str) -> dict:
        """Authorization-code exchange. `code` must already be URL-decoded."""
        return self._post({
            "client_key": self.client_key,
            "client_secret": self.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        })

    def refresh(self, refresh_token: str) -> dict:
        return self._post({
            "client_key": self.client_key,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        })

    def refresher(self):
        """Callable for TokenStore.access_token()."""
        def _refresh(store: TokenStore, row) -> None:
            if not row.refresh_token:
                store.mark_reauth_required(
                    row.platform, row.account_ref, "no refresh token stored")
                raise ReauthRequired("no refresh token stored")
            apply_tiktok_refresh(store, row, self.refresh(row.refresh_token))
        return _refresh


class TikTokOfficialAdapter(DataSourceAdapter):
    platform = "tiktok"
    official = True

    def __init__(self, store: TokenStore, auth: TikTokAuth,
                 client: httpx.Client | None = None):
        self.store = store
        self.auth = auth
        self.http = client or httpx.Client(timeout=60.0)

    def _headers(self, account_ref: str) -> dict:
        token = self.store.access_token(self.platform, account_ref,
                                        refresher=self.auth.refresher())
        return {"Authorization": f"Bearer {token}",
                "Content-Type": "application/json"}

    def _video_list_page(self, account_ref: str, cursor: int | None,
                         max_count: int = 20) -> dict:
        body: dict = {"max_count": max_count}
        if cursor:
            body["cursor"] = cursor
        r = self.http.post(
            f"{API}/video/list/", params={"fields": ",".join(VIDEO_FIELDS)},
            json=body, headers=self._headers(account_ref))
        if r.status_code == 401:
            self.store.mark_reauth_required(
                self.platform, account_ref, "video/list returned 401")
            raise ReauthRequired("tiktok rejected the access token")
        r.raise_for_status()
        payload = r.json()
        err = (payload.get("error") or {})
        if err.get("code") not in (None, "ok"):
            raise RuntimeError(f"tiktok video/list error: {err.get('code')}: "
                               f"{err.get('message')}")
        return payload.get("data", {})

    def _iter_raw(self, account_ref: str) -> Iterator[dict]:
        cursor, seen = None, 0
        while True:
            data = self._video_list_page(account_ref, cursor)
            videos = data.get("videos") or []
            for v in videos:
                seen += 1
                yield v
            if not data.get("has_more") or not videos:
                break
            cursor = data.get("cursor")
            if cursor is None:
                break
        logger.info("tiktok: listed %d video(s) for %s", seen, account_ref)

    # -- adapter interface ------------------------------------------------

    def fetch_account_videos(self, account_ref: str, *,
                             since: datetime | None = None
                             ) -> Iterator[NormalizedVideo]:
        for raw in self._iter_raw(account_ref):
            posted = _epoch(raw.get("create_time"))
            if since and posted and posted < since:
                continue
            desc = raw.get("video_description") or raw.get("title") or ""
            yield NormalizedVideo(
                external_id=str(raw["id"]), platform=self.platform,
                caption=desc or None,
                hashtags=_hashtags(desc),
                posted_at=posted,
                duration_s=_num(raw.get("duration")),
                # Display API returns no downloadable media (ADR 0001).
                media_url=None, media_url_expires=False,
                raw=raw,
            )

    def fetch_metrics(self, account_ref: str, external_ids: list[str],
                      ) -> Iterator[NormalizedMetrics]:
        """Metrics arrive on the same video/list response, so this re-lists
        rather than issuing a second kind of call."""
        wanted = set(external_ids) if external_ids else None
        now = datetime.now(timezone.utc)
        for raw in self._iter_raw(account_ref):
            ext = str(raw["id"])
            if wanted is not None and ext not in wanted:
                continue
            yield NormalizedMetrics(
                external_id=ext, captured_at=now,
                views=_int(raw.get("view_count")),
                likes=_int(raw.get("like_count")),
                comments=_int(raw.get("comment_count")),
                shares=_int(raw.get("share_count")),
                # Saves are not exposed by the Display API. NULL, never 0:
                # a zero would be indistinguishable from a real zero.
                saves=None,
                is_rounded=False,     # official API reports exact counts
                raw={k: raw.get(k) for k in
                     ("view_count", "like_count", "comment_count",
                      "share_count")},
            )


# ---------------------------------------------------------------------------

def _epoch(value) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _int(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _num(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _hashtags(text: str) -> list[str]:
    import re
    return [m.lower() for m in re.findall(r"#(\w+)", text or "")]
