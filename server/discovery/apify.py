"""Apify DiscoveryAdapter.

Every field mapped below was **observed in a recorded live response** from
the Phase 0A probe (`probes/data_access/sample_*.json`, gitignored because
they contain third-party data). Nothing here is taken from a pricing page or
a blog post, and nothing is mapped that was not actually present - which is
the rule that matters, because a confidently-wrong field name produces nulls
that look like missing data rather than a bug.

Endpoints, verified by `probes/data_access/fetch_apify.py` having run live
against them:

    POST /v2/acts/{actor}/runs          start a run
    GET  /v2/actor-runs/{id}            poll status
    POST /v2/actor-runs/{id}/abort      stop a run
    GET  /v2/datasets/{id}/items        fetch results

Actual billed cost comes from the run object's `usageTotalUsd`, falling back
to `costUsd`.

Four actors were validated in Phase 0A. Their measured differences drive
which one is used for what:

| actor | platform | views | history | notes |
|---|---|---|---|---|
| `apidojo/tiktok-scraper` | TikTok | **exact** | profile/keyword | direct mp4, followers, music; free plan caps a run at 10 items |
| `clockworks/free-tiktok-scraper` | TikTok | **rounded ~3 s.f.** | profile/hashtag | followers, bio, bio link |
| `apify/instagram-reel-scraper` | Instagram | exact | **full back-catalogue** | **no follower count, no bio** |
| `apify/instagram-hashtag-scraper` | Instagram | exact | **recent only** | 0 of 45 records were >=35 days old |

So: hashtags discover creators, profiles measure them. The adapter encodes
that rather than leaving it to the caller to remember.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone

import httpx

from server.discovery.base import (DiscoveredCreator, DiscoveredPublication,
                                   DiscoveryAdapter, DiscoveryError,
                                   DiscoveryQuery, DiscoveryResult, Platform,
                                   ProviderCapabilities, QueryKind)

logger = logging.getLogger("virallab.discovery.apify")

API = "https://api.apify.com/v2"

#: Actors validated in Phase 0A. Using an unvalidated actor would mean
#: mapping fields nobody has seen.
ACTOR_TIKTOK_EXACT = "apidojo/tiktok-scraper"
ACTOR_TIKTOK_ROUNDED = "clockworks/free-tiktok-scraper"
ACTOR_IG_PROFILE = "apify/instagram-reel-scraper"
ACTOR_IG_HASHTAG = "apify/instagram-hashtag-scraper"

#: `[PRIMARY, measured 0A]` cost per 1,000 items, from actual billing.
COST_PER_1K_USD = {
    ACTOR_TIKTOK_EXACT: 0.30,
    ACTOR_TIKTOK_ROUNDED: 3.00,
    ACTOR_IG_PROFILE: 2.56,
    ACTOR_IG_HASHTAG: 2.56,
}
#: Smallest charge we assume for any run, so a tiny query still reserves
#: something. An estimate below the real charge is how a cap is exceeded.
MIN_ESTIMATE_USD = 0.05
#: Multiplier on the measured rate when estimating. Deliberately above 1:
#: reserving slightly too much is recoverable, reserving too little is not.
ESTIMATE_SAFETY = 1.25

#: Refuse anything larger whatever the caller asks for.
MAX_ITEMS_HARD_CAP = 500
POLL_SECONDS = 5.0
#: A run that has not finished by now is aborted rather than left billing.
RUN_TIMEOUT_SECONDS = 900
MAX_HTTP_ATTEMPTS = 4

TERMINAL_OK = {"SUCCEEDED"}
TERMINAL_BAD = {"FAILED", "ABORTED", "TIMED-OUT", "TIMED_OUT"}


def _epoch(value) -> datetime | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    # Some actors report milliseconds.
    if n > 10_000_000_000:
        n //= 1000
    return datetime.fromtimestamp(n, tz=timezone.utc)


def _iso(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _num(value) -> int | None:
    """An integer count, or None. Never 0 for a missing value."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _links(text: str | None) -> list[str]:
    import re
    if not text:
        return []
    return re.findall(r"https?://[^\s,]+", text)


# ---------------------------------------------------------------------------
# capabilities, from the recorded responses
# ---------------------------------------------------------------------------

CAPS_TIKTOK = ProviderCapabilities(
    provider="apify", platform=Platform.TIKTOK,
    supported_queries=(QueryKind.CREATOR, QueryKind.KEYWORD,
                       QueryKind.HASHTAG),
    follower_count=True, creator_bio=True, creator_verified=True,
    view_count=True, views_rounded=False, saves_count=True,
    shares_count=True, music_metadata=True, media_url=True,
    full_history=True,
    coverage_notes=(
        "apidojo/tiktok-scraper returns exact view counts and a direct mp4 "
        "URL; its free plan caps a run at 10 items regardless of maxItems.",
        "clockworks/free-tiktok-scraper rounds large counts to ~3 "
        "significant figures, which blurs outlier boundaries.",
    ))

CAPS_INSTAGRAM = ProviderCapabilities(
    provider="apify", platform=Platform.INSTAGRAM,
    supported_queries=(QueryKind.CREATOR, QueryKind.HASHTAG),
    # Verified absent from every recorded response of both Instagram
    # actors: they return publications with an owner username and id, and
    # no account-level figures at all.
    follower_count=False, creator_bio=False, creator_verified=False,
    view_count=True, views_rounded=False, saves_count=False,
    shares_count=True, music_metadata=True, media_url=True,
    full_history=True,
    coverage_notes=(
        "The validated Instagram actors return NO follower count and NO "
        "bio. Creator-level figures are unavailable, not zero.",
        "Hashtag search returns only recent content (0 of 45 sampled "
        "records were >=35 days old), so it discovers creators and must "
        "not be used to measure performance.",
        "`videoViewCount` was null in every recorded response; "
        "`videoPlayCount` is the usable play figure.",
    ))


class ApifyClient:
    """Thin HTTP client: run, poll, abort, fetch. Bounded everywhere."""

    def __init__(self, token: str, *, client: httpx.Client | None = None,
                 poll_seconds: float = POLL_SECONDS,
                 run_timeout_seconds: int = RUN_TIMEOUT_SECONDS):
        if not token:
            raise DiscoveryError("no Apify token configured", permanent=True,
                                 provider="apify")
        self.token = token
        self.http = client or httpx.Client(timeout=60.0)
        self.poll_seconds = poll_seconds
        self.run_timeout_seconds = run_timeout_seconds

    def _request(self, method: str, path: str, *, json_body: dict | None = None,
                 params: dict | None = None) -> dict | list:
        """Authenticate with a header, never a query parameter.

        `[PRIMARY]` https://docs.apify.com/api/v2 (checked 2026-10-25, API
        v2) supports both and recommends the header: "Using your token in
        the request header is more secure than using it as a URL parameter
        because URLs are often stored in browser history and server logs."
        An earlier version of this client sent `?token=...`, which put the
        credential in every URL this process built.
        """
        url = f"{API}/{path.lstrip('/')}"
        p = dict(params or {})
        headers = {"Authorization": f"Bearer {self.token}"}
        last: Exception | None = None
        for attempt in range(1, MAX_HTTP_ATTEMPTS + 1):
            try:
                r = self.http.request(method, url, params=p, json=json_body,
                                      headers=headers)
            except httpx.HTTPError as exc:
                last = exc
            else:
                if r.status_code == 429 or r.status_code >= 500:
                    last = DiscoveryError(
                        f"apify {r.status_code} on {method} {path}",
                        provider="apify")
                elif r.status_code >= 400:
                    # 4xx other than 429 will not become true by waiting.
                    raise DiscoveryError(
                        f"apify {r.status_code} on {method} {path}: "
                        f"{r.text[:300]}", permanent=True, provider="apify")
                else:
                    if not r.content:
                        return {}
                    return r.json()
            if attempt < MAX_HTTP_ATTEMPTS:
                # Full jitter, so parallel jobs do not retry in lockstep.
                delay = random.uniform(0, min(30.0, 2.0 ** attempt))
                logger.warning("apify %s %s failed (%s); retry %d/%d in "
                               "%.1fs", method, path, last, attempt,
                               MAX_HTTP_ATTEMPTS, delay)
                time.sleep(delay)
        raise DiscoveryError(f"apify unreachable after {MAX_HTTP_ATTEMPTS} "
                             f"attempts: {last}", provider="apify")

    def run_actor(self, actor: str, payload: dict) -> dict:
        actor_path = actor.replace("/", "~")
        data = self._request("POST", f"acts/{actor_path}/runs",
                             json_body=payload)
        run = (data or {}).get("data") or {}
        if not run.get("id"):
            raise DiscoveryError(f"apify did not return a run id for {actor}",
                                 provider="apify")
        return run

    def wait(self, run_id: str) -> dict:
        """Poll to a terminal state, aborting rather than billing forever."""
        deadline = time.monotonic() + self.run_timeout_seconds
        while True:
            data = self._request("GET", f"actor-runs/{run_id}")
            run = (data or {}).get("data") or {}
            status = run.get("status")
            if status in TERMINAL_OK:
                return run
            if status in TERMINAL_BAD:
                raise DiscoveryError(
                    f"apify run {run_id} ended {status}", provider="apify")
            if time.monotonic() > deadline:
                self.abort(run_id)
                raise DiscoveryError(
                    f"apify run {run_id} exceeded "
                    f"{self.run_timeout_seconds}s and was aborted",
                    provider="apify")
            time.sleep(self.poll_seconds)

    def abort(self, run_id: str) -> None:
        try:
            self._request("POST", f"actor-runs/{run_id}/abort")
        except DiscoveryError:
            logger.warning("could not abort apify run %s; it may keep "
                           "billing", run_id)

    def dataset_items(self, dataset_id: str, *, limit: int,
                      offset: int = 0) -> list[dict]:
        data = self._request("GET", f"datasets/{dataset_id}/items",
                             params={"limit": limit, "offset": offset,
                                     "clean": "true"})
        return data if isinstance(data, list) else []

    @staticmethod
    def run_cost_usd(run: dict) -> float | None:
        for key in ("usageTotalUsd", "costUsd"):
            v = run.get(key)
            if isinstance(v, (int, float)):
                return float(v)
        return None

    def settled_cost_usd(self, run_id: str, *, expected_items: int = 0,
                         attempts: int = 8,
                         delay: float = 4.0) -> float | None:
        """The billed cost, after Apify has finished accounting for it.

        MEASURED 2026-10-26: the run object returned by the final status
        poll carries `usageTotalUsd: 0`, and the real figure appears up to
        half a minute later. Reading it from that poll recorded $0.00 for a
        run Apify actually billed $0.0030 - so every discovery job looked
        free and the monthly cap had nothing real to count.

        A zero total is only believed when the run also charged **no
        events**. `chargedEventCounts` is the discriminator: a run that
        charged ten `apify-default-dataset-item` events and reports $0.00
        has not settled yet, whereas a genuinely free run has neither. That
        distinction is what stops "not reported yet" being recorded as
        "free", which is the error that makes a spending cap decorative.

        `None` means Apify never reported one. The caller must not turn
        that into zero.
        """
        last: float | None = None
        for attempt in range(attempts):
            try:
                data = self._request("GET", f"actor-runs/{run_id}")
                run = (data or {}).get("data") or {}
            except DiscoveryError:
                break

            cost = self.run_cost_usd(run)
            charged = run.get("chargedEventCounts") or {}
            billable = sum(v for v in charged.values()
                           if isinstance(v, (int, float)))

            if cost:
                return cost
            # A zero is only believed when the run cannot have been
            # charged. `chargedEventCounts` is empty immediately after a
            # run finishes - the same moment `usageTotalUsd` is still 0 -
            # so it cannot distinguish "free" from "not settled" on its
            # own. The item count can: a run that returned rows billed for
            # them.
            if cost == 0.0 and not billable and expected_items <= 0:
                return 0.0
            last = cost if cost is not None else last
            if attempt < attempts - 1:
                logger.debug("apify run %s charged %d event(s) but reports "
                             "no cost yet; waiting", run_id, billable)
                time.sleep(delay)

        logger.warning(
            "apify run %s returned %d item(s) but never settled a cost "
            "after %.0fs; recording it as unknown rather than free",
            run_id, expected_items, attempts * delay)
        return None


# ---------------------------------------------------------------------------
# normalisers - only fields present in the recorded responses
# ---------------------------------------------------------------------------

def creator_from_apidojo(item: dict) -> DiscoveredCreator | None:
    ch = item.get("channel")
    if not isinstance(ch, dict) or not ch.get("username"):
        return None
    return DiscoveredCreator(
        platform=Platform.TIKTOK,
        username=str(ch["username"]).lstrip("@").lower(),
        external_id=str(ch.get("id")) if ch.get("id") else None,
        display_name=ch.get("name"),
        profile_url=ch.get("url"),
        # This actor carries no bio field; clockworks does.
        bio=None,
        follower_count=_num(ch.get("followers")),
        following_count=_num(ch.get("following")),
        publication_count=_num(ch.get("videos")),
        total_likes=_num(ch.get("likes")),
        verified=ch.get("verified") if isinstance(ch.get("verified"), bool)
        else None,
        counts_rounded=False,
        unavailable_fields=["bio"],
        raw=ch)


def publication_from_apidojo(item: dict) -> DiscoveredPublication | None:
    if not item.get("id"):
        return None
    ch = item.get("channel") or {}
    video = item.get("video") or {}
    song = item.get("song") or {}
    return DiscoveredPublication(
        platform=Platform.TIKTOK,
        external_id=str(item["id"]),
        creator_username=str(ch.get("username", "")).lstrip("@").lower(),
        published_at=_epoch(item.get("uploadedAt")),
        caption=item.get("title"),
        hashtags=[str(h).lstrip("#").lower()
                  for h in (item.get("hashtags") or []) if isinstance(h, str)],
        duration_s=(float(video["duration"])
                    if isinstance(video.get("duration"), (int, float))
                    else None),
        url=item.get("postPage"),
        thumbnail_url=video.get("cover") or video.get("thumbnail"),
        media_url=video.get("url"),
        views=_num(item.get("views")),
        likes=_num(item.get("likes")),
        comments=_num(item.get("comments")),
        shares=_num(item.get("shares")),
        # `bookmarks` is this actor's saves field.
        saves=_num(item.get("bookmarks")),
        counts_rounded=False,
        music_title=song.get("title"),
        music_artist=song.get("artist"),
        music_id=str(song["id"]) if song.get("id") else None,
        raw=item)


def creator_from_clockworks(item: dict) -> DiscoveredCreator | None:
    am = item.get("authorMeta")
    if not isinstance(am, dict) or not am.get("name"):
        return None
    bio = am.get("signature")
    links = _links(bio)
    if am.get("bioLink"):
        links.append(str(am["bioLink"]))
    return DiscoveredCreator(
        platform=Platform.TIKTOK,
        username=str(am["name"]).lstrip("@").lower(),
        external_id=str(am.get("id")) if am.get("id") else None,
        display_name=am.get("nickName"),
        profile_url=am.get("profileUrl"),
        bio=bio,
        bio_links=sorted(set(links)),
        follower_count=_num(am.get("fans")),
        following_count=_num(am.get("following")),
        publication_count=_num(am.get("video")),
        total_likes=_num(am.get("heart")),
        verified=am.get("verified") if isinstance(am.get("verified"), bool)
        else None,
        # This actor expands the abbreviated on-screen figure.
        counts_rounded=True,
        raw=am)


def publication_from_clockworks(item: dict) -> DiscoveredPublication | None:
    if not item.get("id"):
        return None
    am = item.get("authorMeta") or {}
    music = item.get("musicMeta") or {}
    vm = item.get("videoMeta") or {}
    tags = []
    for h in item.get("hashtags") or []:
        if isinstance(h, dict) and h.get("name"):
            tags.append(str(h["name"]).lstrip("#").lower())
        elif isinstance(h, str):
            tags.append(h.lstrip("#").lower())
    return DiscoveredPublication(
        platform=Platform.TIKTOK,
        external_id=str(item["id"]),
        creator_username=str(am.get("name", "")).lstrip("@").lower(),
        published_at=_iso(item.get("createTimeISO"))
        or _epoch(item.get("createTime")),
        caption=item.get("text"),
        hashtags=tags,
        duration_s=(float(vm["duration"])
                    if isinstance(vm.get("duration"), (int, float)) else None),
        url=item.get("webVideoUrl"),
        views=_num(item.get("playCount")),
        likes=_num(item.get("diggCount")),
        comments=_num(item.get("commentCount")),
        shares=_num(item.get("shareCount")),
        saves=_num(item.get("collectCount")),
        counts_rounded=True,
        music_title=music.get("musicName"),
        music_artist=music.get("musicAuthor"),
        music_id=str(music["musicId"]) if music.get("musicId") else None,
        music_is_original=music.get("musicOriginal")
        if isinstance(music.get("musicOriginal"), bool) else None,
        raw=item)


def creator_from_instagram(item: dict) -> DiscoveredCreator | None:
    """Instagram creators come only as the owner of a publication.

    Both validated actors return `ownerUsername`, `ownerFullName` and
    `ownerId` and nothing else about the account - no followers, no bio.
    Those are listed as unavailable so nothing downstream reads a zero.
    """
    user = item.get("ownerUsername")
    if not user:
        return None
    return DiscoveredCreator(
        platform=Platform.INSTAGRAM,
        username=str(user).lstrip("@").lower(),
        external_id=str(item["ownerId"]) if item.get("ownerId") else None,
        display_name=item.get("ownerFullName"),
        profile_url=f"https://www.instagram.com/{str(user).lstrip('@')}/",
        follower_count=None,
        unavailable_fields=["follower_count", "bio", "verified",
                            "following_count", "publication_count",
                            "total_likes"],
        raw={k: item.get(k) for k in
             ("ownerUsername", "ownerFullName", "ownerId")})


def publication_from_instagram(item: dict) -> DiscoveredPublication | None:
    if not item.get("id"):
        return None
    music = item.get("musicInfo") or {}
    return DiscoveredPublication(
        platform=Platform.INSTAGRAM,
        external_id=str(item["id"]),
        creator_username=str(item.get("ownerUsername", "")
                             ).lstrip("@").lower(),
        published_at=_iso(item.get("timestamp")),
        caption=item.get("caption"),
        hashtags=[str(h).lstrip("#").lower()
                  for h in (item.get("hashtags") or []) if isinstance(h, str)],
        duration_s=(float(item["videoDuration"])
                    if isinstance(item.get("videoDuration"), (int, float))
                    else None),
        url=item.get("url"),
        thumbnail_url=item.get("displayUrl"),
        media_url=item.get("videoUrl"),
        # `videoViewCount` was null in every recorded response; play count
        # is the usable figure.
        views=_num(item.get("videoPlayCount")),
        likes=_num(item.get("likesCount")),
        comments=_num(item.get("commentsCount")),
        shares=_num(item.get("sharesCount")),
        # Not exposed by either Instagram actor.
        saves=None,
        counts_rounded=False,
        music_title=music.get("song_name"),
        music_artist=music.get("artist_name"),
        music_id=str(music["audio_id"]) if music.get("audio_id") else None,
        music_is_original=music.get("uses_original_audio")
        if isinstance(music.get("uses_original_audio"), bool) else None,
        unavailable_fields=["saves"],
        raw=item)


NORMALISERS = {
    ACTOR_TIKTOK_EXACT: (creator_from_apidojo, publication_from_apidojo),
    ACTOR_TIKTOK_ROUNDED: (creator_from_clockworks,
                           publication_from_clockworks),
    ACTOR_IG_PROFILE: (creator_from_instagram, publication_from_instagram),
    ACTOR_IG_HASHTAG: (creator_from_instagram, publication_from_instagram),
}


def actor_for(query: DiscoveryQuery) -> str:
    """Which validated actor serves this query.

    Encodes the Phase 0A finding rather than leaving it to the caller:
    hashtags discover creators, profiles measure them.
    """
    if query.platform is Platform.TIKTOK:
        return ACTOR_TIKTOK_EXACT
    if query.kind is QueryKind.HASHTAG:
        return ACTOR_IG_HASHTAG
    return ACTOR_IG_PROFILE


def build_input(query: DiscoveryQuery, actor: str, limit: int) -> dict:
    """Actor input, using only keys seen in the Phase 0A input files."""
    value = query.normalised_value()
    if actor == ACTOR_TIKTOK_EXACT:
        if query.kind is QueryKind.CREATOR:
            return {"startUrls": [f"https://www.tiktok.com/@{value}"],
                    "maxItems": limit}
        if query.kind is QueryKind.HASHTAG:
            return {"startUrls": [f"https://www.tiktok.com/tag/{value}"],
                    "maxItems": limit, "sortType": "RELEVANCE"}
        return {"keywords": [value], "maxItems": limit,
                "sortType": "RELEVANCE"}
    if actor == ACTOR_TIKTOK_ROUNDED:
        if query.kind is QueryKind.HASHTAG:
            return {"hashtags": [value], "resultsPerPage": limit,
                    "shouldDownloadVideos": False,
                    "shouldDownloadCovers": False,
                    "shouldDownloadSubtitles": False,
                    "shouldDownloadSlideshowImages": False}
        return {"profiles": [value], "resultsPerPage": limit,
                "shouldDownloadVideos": False,
                "shouldDownloadCovers": False,
                "shouldDownloadSubtitles": False,
                "shouldDownloadSlideshowImages": False}
    if actor == ACTOR_IG_HASHTAG:
        return {"hashtags": [value], "resultsType": "reels",
                "resultsLimit": limit}
    # ACTOR_IG_PROFILE
    return {"username": [value], "resultsLimit": limit}


class ApifyDiscoveryAdapter(DiscoveryAdapter):
    """Live Apify adapter. Costs money; gated by the budget reservation."""

    name = "apify"
    is_fixture = False

    def __init__(self, token: str, *, client: ApifyClient | None = None,
                 max_items_cap: int = MAX_ITEMS_HARD_CAP):
        self.client = client or ApifyClient(token)
        self.max_items_cap = max_items_cap

    def capabilities(self, platform: Platform) -> ProviderCapabilities:
        return (CAPS_TIKTOK if platform is Platform.TIKTOK
                else CAPS_INSTAGRAM)

    def estimate_usd(self, query: DiscoveryQuery) -> float:
        actor = actor_for(query)
        per_1k = COST_PER_1K_USD.get(actor, 3.00)
        limit = min(max(1, query.limit), self.max_items_cap)
        raw = per_1k * limit / 1000.0 * ESTIMATE_SAFETY
        return round(max(MIN_ESTIMATE_USD, raw), 4)

    def search(self, query: DiscoveryQuery) -> DiscoveryResult:
        caps = self.capabilities(query.platform)
        if query.kind not in caps.supported_queries:
            raise DiscoveryError(
                f"apify cannot run a {query.kind.value} query on "
                f"{query.platform.value}", permanent=True, provider="apify")

        actor = actor_for(query)
        limit = min(max(1, query.limit), self.max_items_cap)
        observed_at = datetime.now(timezone.utc)

        run = self.client.run_actor(actor, build_input(query, actor, limit))
        run_id = run["id"]
        try:
            finished = self.client.wait(run_id)
        except DiscoveryError:
            # The run may still be billing; stop it before giving up.
            self.client.abort(run_id)
            raise
        dataset_id = finished.get("defaultDatasetId")
        items = (self.client.dataset_items(dataset_id, limit=limit)
                 if dataset_id else [])

        # Re-read after the dataset fetch: by then Apify has settled the
        # usage figure that the final status poll reports as zero.
        billed = self.client.settled_cost_usd(
            run_id, expected_items=len(items))
        if billed is None:
            logger.warning(
                "apify reported no cost for run %s; the estimate is NOT "
                "substituted - the job records an unknown actual cost",
                run_id)
        result = self.normalise(query, actor, items, observed_at,
                                actual_usd=billed)
        result.estimated_usd = self.estimate_usd(query)
        result.provider_run_id = run_id
        return result

    def normalise(self, query: DiscoveryQuery, actor: str,
                  items: list[dict], observed_at: datetime, *,
                  actual_usd: float | None = None,
                  is_fixture: bool = False) -> DiscoveryResult:
        """Shape raw actor output. Separated so fixtures use the same path.

        That matters: a fixture that went through a different normaliser
        would prove nothing about the live one.
        """
        caps = self.capabilities(query.platform)
        to_creator, to_pub = NORMALISERS[actor]

        creators: dict[str, DiscoveredCreator] = {}
        publications: list[DiscoveredPublication] = []
        item_errors: list[dict] = []

        for item in items:
            if not isinstance(item, dict):
                continue
            # MEASURED 2026-10-26: `apidojo/tiktok-scraper` answers a query
            # it found nothing for with sentinel rows `{"noResults": true}`
            # - and Apify BILLS for them. A search that returns nothing
            # costs the same $0.0030 as one that returns ten posts, so an
            # empty result has to be reported as a provider answer rather
            # than silently stored as an empty success.
            if item.get("noResults"):
                item_errors.append({
                    "reason": "provider_found_nothing",
                    "detail": ("the actor returned its no-results sentinel: "
                               "this query matched nothing. The run was "
                               "still billed."),
                })
                continue
            # The Instagram reel actor reports inaccessible accounts as an
            # item with `error`. Dropping those would turn a private account
            # into an apparently empty one.
            if item.get("error"):
                item_errors.append({
                    "error": str(item.get("error"))[:200],
                    "description": str(
                        item.get("errorDescription") or "")[:300],
                    "input": str(item.get("url") or item.get("username")
                                 or item.get("inputUrl") or "")[:200],
                })
                continue
            c = to_creator(item)
            if c is not None and c.username and c.username not in creators:
                creators[c.username] = c
            p = to_pub(item)
            if p is not None:
                publications.append(p)

        notes = list(caps.coverage_notes)
        if query.kind is QueryKind.HASHTAG:
            notes.append(
                "This result came from a hashtag feed, which skews to "
                "recently promoted content. Use it to find creators, not to "
                "measure them.")
        if len(items) >= limit_of(query):
            notes.append(
                f"Result hit the requested limit of {limit_of(query)}; there "
                f"may be more.")

        return DiscoveryResult(
            provider=self.name, query=query, observed_at=observed_at,
            creators=list(creators.values()), publications=publications,
            actual_usd=actual_usd, item_errors=item_errors,
            coverage_notes=notes, capabilities=caps, is_fixture=is_fixture,
            truncated=len(items) >= limit_of(query))


def limit_of(query: DiscoveryQuery) -> int:
    return min(max(1, query.limit), MAX_ITEMS_HARD_CAP)
