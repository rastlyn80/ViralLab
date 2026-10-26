"""Paid delivery, from the Marketing API. Inert until `ads_read` exists.

Track B of ADR 0008. The organic half of this account is already measured;
this is the other half, and it is the only thing that can turn the surface
residual from an upper bound on paid exposure into the paid figure itself.

**Verified against primary sources, 2026-10-26** (ADR 0008 §2):

- Ads Insights is available per ad at `/{ad-id}/insights` and **requires
  the `ads_read` permission**
  (https://developers.facebook.com/docs/marketing-api/insights, v26.0).
- `video_p25_watched_actions` … `video_p100_watched_actions` exist; they do
  not support the `region` breakdown and no `video_*` field may be combined
  with an hourly-stats breakdown
  (https://developers.facebook.com/docs/marketing-api/insights/breakdowns/).
- `adcreative.effective_instagram_media_id` is "The ID of an Instagram post
  to use in an ad"
  (https://developers.facebook.com/docs/marketing-api/reference/ad-creative/,
  v26.0). This is the join from an ad back to the organic publication.

**Measured on this account, 2026-10-26**: the stored credential is a Page
token whose scopes are `pages_show_list`, `business_management`,
`instagram_basic`, `instagram_manage_insights`, `pages_read_engagement`,
`public_profile`. There is no `ads_read`, and `/me/adaccounts` is not an
edge of a Page token at all — it returned `(#100) Tried accessing
nonexisting field (adaccounts)`. So every entry point here raises
`AdsScopeMissing` today, by design, rather than failing somewhere deeper
with a confusing error.

**`[OPEN]`, and it must stay open until rows arrive.** No
platform-official sentence was found confirming that a boost created
*inside the Instagram app* surfaces as an ad with an
`effective_instagram_media_id`. The mechanism is documented for ads created
through the API. It is very likely to hold, and it is not verified, so
`discover_boosted_media()` reports what it actually found rather than
assuming the join succeeded.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.collectors.instagram_official import GRAPH, DEFAULT_VERSION
from server.collectors.tokens import TokenStore
from server.db.models import AdExposure, Video

logger = logging.getLogger("virallab.collectors.meta_ads")

METHOD_VERSION = "meta-ads-v1"

#: Required to read Ads Insights at all. Verified: the Ads Insights
#: overview states "The ads_read permission" is required.
REQUIRED_SCOPE = "ads_read"

#: Ad-level fields. `time_increment=1` turns this into one row per day,
#: which is what `ad_exposures` stores — a lifetime total could not be
#: age-anchored and could not be attributed to a boost window.
INSIGHT_FIELDS = [
    "ad_id", "adset_id", "campaign_id", "account_currency",
    "spend", "impressions", "reach",
    "video_p25_watched_actions", "video_p50_watched_actions",
    "video_p75_watched_actions", "video_p95_watched_actions",
    "video_p100_watched_actions", "video_avg_time_watched_actions",
]


class AdsScopeMissing(RuntimeError):
    """The credential cannot read ads. A person must re-authorise.

    Deliberately not a transient error: retrying cannot add a scope, and
    treating it as transient would turn an authorisation gap into a retry
    loop (the same distinction ADR 0005 makes for re-auth).
    """


@dataclass(frozen=True)
class AdRow:
    ad_id: str
    ad_account_id: str
    date_start: date
    date_stop: date
    adset_id: str | None = None
    campaign_id: str | None = None
    effective_instagram_media_id: str | None = None
    spend: float | None = None
    currency: str | None = None
    impressions: int | None = None
    reach: int | None = None
    video_p25: int | None = None
    video_p50: int | None = None
    video_p75: int | None = None
    video_p95: int | None = None
    video_p100: int | None = None
    video_avg_time_watched_s: float | None = None
    raw: dict | None = None

    @property
    def idempotency_key(self) -> str:
        """`{job_type}:{id}:{method_version}` per CLAUDE.md §4.1.

        Keyed on the reporting day as well as the ad, because Ads Insights
        revises figures for days after the fact: re-collecting a day must
        update that day's row, not append a second one.
        """
        return f"ads:{self.ad_id}:{self.date_start.isoformat()}:" \
               f"{METHOD_VERSION}"


def _action_value(values: object) -> int | None:
    """`video_p25_watched_actions` is a list of {action_type, value}.

    Meta returns an action breakdown even where only one action type
    applies. Summing is wrong when several types are present and taking the
    first is wrong when the order varies, so this takes `video_view`
    explicitly and falls back to a lone entry.
    """
    if not isinstance(values, list) or not values:
        return None
    for item in values:
        if isinstance(item, dict) and item.get("action_type") == "video_view":
            try:
                return int(float(item.get("value")))
            except (TypeError, ValueError):
                return None
    if len(values) == 1 and isinstance(values[0], dict):
        try:
            return int(float(values[0].get("value")))
        except (TypeError, ValueError):
            return None
    logger.warning("ambiguous action breakdown with %d entries; not read",
                   len(values))
    return None


class MetaAdsAdapter:
    """Read-only Ads Insights access. Writes nothing to Meta."""

    platform = "instagram"

    def __init__(self, store: TokenStore, *, account_ref: str,
                 version: str = DEFAULT_VERSION,
                 client: httpx.Client | None = None):
        self.store = store
        self.account_ref = account_ref
        self.version = version
        self.http = client or httpx.Client(timeout=60.0)

    # -- scope ------------------------------------------------------------

    def scopes(self) -> list[str]:
        token = self.store.access_token(self.platform, self.account_ref)
        r = self.http.get(f"{GRAPH}/{self.version}/debug_token",
                          params={"input_token": token,
                                  "access_token": token})
        body = r.json() if r.content else {}
        return list((body.get("data") or {}).get("scopes") or [])

    def require_scope(self) -> None:
        got = self.scopes()
        if REQUIRED_SCOPE not in got:
            raise AdsScopeMissing(
                f"the stored credential has no {REQUIRED_SCOPE!r} scope "
                f"(it has: {', '.join(got) or 'none'}). Paid metrics cannot "
                f"be read until the owner re-authorises with ads_read; see "
                f"docs/adr/0008-paid-organic-separation.md §4.")

    # -- reads ------------------------------------------------------------

    def _get(self, path: str, **params) -> dict:
        token = self.store.access_token(self.platform, self.account_ref)
        r = self.http.get(f"{GRAPH}/{self.version}/{path.lstrip('/')}",
                          params=params,
                          headers={"Authorization": f"Bearer {token}"})
        payload = r.json() if r.content else {}
        err = payload.get("error") or {}
        if err:
            code, msg = err.get("code"), err.get("message", "")
            if code in (10, 200, 294) or "permission" in msg.lower():
                raise AdsScopeMissing(f"Meta error {code}: {msg}")
            raise RuntimeError(f"Meta error {code}: {msg}")
        r.raise_for_status()
        return payload

    def ad_accounts(self) -> list[dict]:
        self.require_scope()
        payload = self._get("me/adaccounts",
                            fields="id,name,account_status,currency")
        return payload.get("data", [])

    def ads_with_creatives(self, ad_account_id: str) -> dict[str, dict]:
        """ad_id -> {creative media id, adset, campaign}.

        One call per page rather than one per ad: the creative is requested
        as a nested field so a hundred ads do not become a hundred round
        trips.
        """
        self.require_scope()
        out: dict[str, dict] = {}
        path = f"{ad_account_id}/ads"
        params = {"fields": "id,adset_id,campaign_id,"
                            "creative{effective_instagram_media_id,"
                            "effective_object_story_id}",
                  "limit": 100}
        while path:
            payload = self._get(path, **params)
            for item in payload.get("data", []):
                creative = item.get("creative") or {}
                out[str(item["id"])] = {
                    "adset_id": item.get("adset_id"),
                    "campaign_id": item.get("campaign_id"),
                    "effective_instagram_media_id":
                        creative.get("effective_instagram_media_id"),
                }
            nxt = (payload.get("paging") or {}).get("cursors", {}).get("after")
            if not nxt:
                break
            params = dict(params, after=nxt)
        return out

    def ad_insights(self, ad_account_id: str, *, since: date, until: date,
                    ) -> list[AdRow]:
        """One row per ad per day over the window."""
        self.require_scope()
        creatives = self.ads_with_creatives(ad_account_id)

        rows: list[AdRow] = []
        path = f"{ad_account_id}/insights"
        params = {
            "level": "ad",
            "fields": ",".join(INSIGHT_FIELDS),
            "time_increment": 1,
            "time_range": f'{{"since":"{since.isoformat()}",'
                          f'"until":"{until.isoformat()}"}}',
            "limit": 200,
        }
        while path:
            payload = self._get(path, **params)
            for item in payload.get("data", []):
                ad_id = str(item.get("ad_id"))
                meta = creatives.get(ad_id, {})
                avg = _action_value(
                    item.get("video_avg_time_watched_actions"))
                rows.append(AdRow(
                    ad_id=ad_id,
                    ad_account_id=ad_account_id,
                    date_start=date.fromisoformat(item["date_start"]),
                    date_stop=date.fromisoformat(item["date_stop"]),
                    adset_id=meta.get("adset_id") or item.get("adset_id"),
                    campaign_id=(meta.get("campaign_id")
                                 or item.get("campaign_id")),
                    effective_instagram_media_id=meta.get(
                        "effective_instagram_media_id"),
                    spend=(float(item["spend"]) if item.get("spend")
                           is not None else None),
                    currency=item.get("account_currency"),
                    impressions=(int(item["impressions"])
                                 if item.get("impressions") is not None
                                 else None),
                    reach=(int(item["reach"]) if item.get("reach")
                           is not None else None),
                    video_p25=_action_value(
                        item.get("video_p25_watched_actions")),
                    video_p50=_action_value(
                        item.get("video_p50_watched_actions")),
                    video_p75=_action_value(
                        item.get("video_p75_watched_actions")),
                    video_p95=_action_value(
                        item.get("video_p95_watched_actions")),
                    video_p100=_action_value(
                        item.get("video_p100_watched_actions")),
                    video_avg_time_watched_s=(float(avg) if avg is not None
                                              else None),
                    raw=item,
                ))
            nxt = (payload.get("paging") or {}).get("cursors", {}).get("after")
            if not nxt:
                break
            params = dict(params, after=nxt)
        return rows


def record_ad_exposures(session: Session, rows: list[AdRow], *,
                        platform: str = "instagram") -> dict:
    """Upsert ad rows, resolving each to a publication where possible.

    Upsert on `idempotency_key`, never insert-only: Ads Insights revises a
    day's figures after the fact, so the second collection of a day is a
    correction and must replace the first (CLAUDE.md §4.1).
    """
    media_ids = {r.effective_instagram_media_id for r in rows
                 if r.effective_instagram_media_id}
    by_media: dict[str, int] = {}
    if media_ids:
        by_media = {v.external_id: v.id for v in session.scalars(
            select(Video).where(Video.platform == platform,
                                Video.external_id.in_(media_ids))).all()}

    inserted = updated = unmatched = 0
    for r in rows:
        vid = (by_media.get(r.effective_instagram_media_id)
               if r.effective_instagram_media_id else None)
        if vid is None:
            unmatched += 1
        existing = session.scalar(select(AdExposure).where(
            AdExposure.idempotency_key == r.idempotency_key))
        if existing is None:
            existing = AdExposure(idempotency_key=r.idempotency_key,
                                  method_version=METHOD_VERSION)
            session.add(existing)
            inserted += 1
        else:
            updated += 1
        existing.video_id = vid
        existing.platform = platform
        existing.ad_account_id = r.ad_account_id
        existing.ad_id = r.ad_id
        existing.adset_id = r.adset_id
        existing.campaign_id = r.campaign_id
        existing.effective_instagram_media_id = \
            r.effective_instagram_media_id
        existing.date_start = r.date_start
        existing.date_stop = r.date_stop
        existing.spend = r.spend
        existing.currency = r.currency
        existing.impressions = r.impressions
        existing.reach = r.reach
        existing.video_p25 = r.video_p25
        existing.video_p50 = r.video_p50
        existing.video_p75 = r.video_p75
        existing.video_p95 = r.video_p95
        existing.video_p100 = r.video_p100
        existing.video_avg_time_watched_s = r.video_avg_time_watched_s
        existing.raw = r.raw
        existing.collected_at = datetime.now(timezone.utc)
    session.flush()
    summary = {"rows": len(rows), "inserted": inserted, "updated": updated,
               "unmatched_to_publication": unmatched,
               "method_version": METHOD_VERSION}
    logger.info("ad exposures recorded: %s", summary)
    return summary
