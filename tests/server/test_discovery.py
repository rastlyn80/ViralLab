"""DiscoveryAdapter: field mapping, declared capabilities, error handling.

The field names asserted here come from **recorded live Apify responses**
(Phase 0A). The point of these tests is not that the code runs, but that it
maps the contract the provider actually has - and that it never invents a
value it did not receive.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from server.discovery import apify
from server.discovery.base import (DiscoveryError, DiscoveryQuery, Platform,
                                   QueryKind, normalise_handle)
from server.discovery.fixture import FixtureDiscoveryAdapter, adapter_for


# ---------------------------------------------------------------------------
# handle resolution
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("@tonyann", "tonyann"),
    ("tonyann", "tonyann"),
    ("TonyAnn", "tonyann"),
    ("https://www.tiktok.com/@tonyann", "tonyann"),
    ("tiktok.com/@tonyann", "tonyann"),
    ("instagram.com/tonyann/", "tonyann"),
    ("https://www.instagram.com/tonyann?hl=en", "tonyann"),
    # A post URL must resolve to the CREATOR, not the post id.
    ("www.tiktok.com/@demo_pianist/video/123", "demo_pianist"),
    ("https://www.instagram.com/someone/reel/Dq00001/", "someone"),
    ("demo.pianist_1", "demo.pianist_1"),
])
def test_handles_and_urls_resolve_to_a_username(raw, expected):
    assert normalise_handle(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "Tony Ann", "a b c",
                                 "https://www.instagram.com/",
                                 "no@good!", "x" * 80])
def test_unusable_input_is_rejected_not_guessed(raw):
    with pytest.raises(ValueError):
        normalise_handle(raw)


def test_query_cache_key_is_stable_and_normalised():
    a = DiscoveryQuery(kind=QueryKind.CREATOR, platform=Platform.TIKTOK,
                       value="@TonyAnn", limit=50)
    b = DiscoveryQuery(kind=QueryKind.CREATOR, platform=Platform.TIKTOK,
                       value="https://www.tiktok.com/@tonyann", limit=50)
    assert a.cache_key() == b.cache_key()


# ---------------------------------------------------------------------------
# declared capabilities - absence is a fact, not a bug
# ---------------------------------------------------------------------------

def test_instagram_reports_no_follower_count():
    """Verified against every recorded response from both Instagram actors:
    they return publications with an owner username and nothing about the
    account. Claiming otherwise would put a fabricated number in the UI."""
    caps = apify.CAPS_INSTAGRAM
    assert caps.follower_count is False
    assert caps.creator_bio is False
    assert "follower_count" in caps.missing_creator_fields()
    assert any("follower" in n.lower() for n in caps.coverage_notes)


def test_tiktok_does_report_followers_and_saves():
    caps = apify.CAPS_TIKTOK
    assert caps.follower_count is True
    assert caps.saves_count is True
    assert caps.media_url is True


def test_hashtag_queries_are_marked_as_recent_only():
    """`[PRIMARY, measured 0A]` 0 of 45 hashtag records were >=35 days old,
    so hashtags discover creators and must not measure them."""
    notes = " ".join(apify.CAPS_INSTAGRAM.coverage_notes).lower()
    assert "recent" in notes
    assert "discover" in notes


def test_unsupported_query_kinds_are_refused_not_attempted():
    a = FixtureDiscoveryAdapter()
    q = DiscoveryQuery(kind=QueryKind.KEYWORD, platform=Platform.INSTAGRAM,
                       value="piano")
    assert a.supports(q) is False
    with pytest.raises(DiscoveryError) as exc:
        a.search(q)
    assert exc.value.permanent is True


# ---------------------------------------------------------------------------
# normalisers, against the real field names
# ---------------------------------------------------------------------------

APIDOJO_ITEM = {
    "id": "7412345678901234567",
    "title": "Chopin Ballade No. 1 #chopin #piano",
    "views": 2905713, "likes": 318221, "comments": 2044, "shares": 9881,
    "bookmarks": 41222,
    "hashtags": ["chopin", "piano"],
    "channel": {"id": "700000001", "name": "Someone", "username": "someone",
                "verified": True, "url": "https://www.tiktok.com/@someone",
                "followers": 184300, "following": 212, "videos": 341,
                "likes": 2940000},
    "uploadedAt": 1719830000,
    "video": {"width": 1080, "height": 1920, "duration": 31.4,
              "url": "https://cdn.invalid/v.mp4",
              "cover": "https://cdn.invalid/c.jpg"},
    "song": {"id": "69001", "title": "Ballade No. 1", "artist": "Someone"},
    "postPage": "https://www.tiktok.com/@someone/video/7412345678901234567",
}

CLOCKWORKS_ITEM = {
    "id": "7398888888888888888",
    "text": "Nocturne #piano",
    "createTimeISO": "2026-02-14T19:04:00.000Z",
    "playCount": 54900000, "diggCount": 3100000, "commentCount": 12000,
    "shareCount": 88000, "collectCount": 410000,
    "hashtags": [{"name": "piano"}],
    "authorMeta": {"id": "800", "name": "other_person",
                   "nickName": "Other Person", "verified": False,
                   "signature": "pianist | links https://linktr.ee/other",
                   "bioLink": "https://linktr.ee/other",
                   "fans": 912000, "heart": 48000000, "video": 620,
                   "following": 88, "friends": 2},
    "musicMeta": {"musicName": "Nocturne", "musicAuthor": "Other Person",
                  "musicOriginal": True, "musicId": "99001"},
    "videoMeta": {"height": 1920, "width": 1080, "duration": 27},
    "webVideoUrl": "https://www.tiktok.com/@other_person/video/739",
}

IG_ITEM = {
    "id": "3100000000000000001",
    "shortCode": "Dq12345",
    "caption": "Clair de Lune #piano",
    "hashtags": ["piano"],
    "url": "https://www.instagram.com/reel/Dq12345/",
    "commentsCount": 57, "likesCount": 3195,
    "videoPlayCount": 45111,
    # Null in every recorded response; playCount is the usable figure.
    "videoViewCount": None,
    "sharesCount": 668,
    "timestamp": "2026-03-02T10:15:00.000Z",
    "ownerFullName": "Someone Else", "ownerUsername": "someone_else",
    "ownerId": "50000001",
    "videoDuration": 31.233,
    "displayUrl": "https://cdn.invalid/ig.jpg",
    "videoUrl": "https://cdn.invalid/ig.mp4",
    "musicInfo": {"artist_name": "Someone Else", "song_name": "Clair de Lune",
                  "uses_original_audio": True, "audio_id": "88001"},
}


def test_apidojo_publication_maps_the_real_fields():
    p = apify.publication_from_apidojo(APIDOJO_ITEM)
    assert p.external_id == "7412345678901234567"
    assert p.views == 2905713
    # `bookmarks` is this actor's saves field.
    assert p.saves == 41222
    assert p.shares == 9881
    assert p.duration_s == pytest.approx(31.4)
    assert p.media_url == "https://cdn.invalid/v.mp4"
    assert p.music_title == "Ballade No. 1"
    assert p.published_at == datetime.fromtimestamp(1719830000,
                                                     tz=timezone.utc)
    assert p.counts_rounded is False


def test_apidojo_creator_carries_followers():
    c = apify.creator_from_apidojo(APIDOJO_ITEM)
    assert c.username == "someone"
    assert c.follower_count == 184300
    assert c.verified is True
    # This actor has no bio field, and says so rather than leaving a
    # misleading empty string.
    assert c.bio is None
    assert "bio" in c.unavailable_fields


def test_clockworks_counts_are_flagged_as_rounded():
    """This actor expands the abbreviated on-screen figure, so 54.9M comes
    back as 54900000. Treating that as exact would blur outlier
    boundaries invisibly."""
    p = apify.publication_from_clockworks(CLOCKWORKS_ITEM)
    assert p.views == 54900000
    assert p.counts_rounded is True
    c = apify.creator_from_clockworks(CLOCKWORKS_ITEM)
    assert c.counts_rounded is True
    assert c.follower_count == 912000


def test_clockworks_bio_links_are_extracted():
    """A bio link is the strongest available evidence for tying a TikTok
    account to an Instagram one, so it is pulled out rather than left
    buried in the raw blob."""
    c = apify.creator_from_clockworks(CLOCKWORKS_ITEM)
    assert "https://linktr.ee/other" in c.bio_links
    assert c.bio and "pianist" in c.bio


def test_instagram_publication_uses_play_count_not_view_count():
    p = apify.publication_from_instagram(IG_ITEM)
    assert p.views == 45111, "videoViewCount is null; playCount is the figure"
    assert p.shares == 668
    # Neither Instagram actor exposes saves.
    assert p.saves is None
    assert "saves" in p.unavailable_fields
    assert p.music_title == "Clair de Lune"
    assert p.music_is_original is True
    assert p.published_at.year == 2026


def test_instagram_creator_declares_what_it_cannot_know():
    c = apify.creator_from_instagram(IG_ITEM)
    assert c.username == "someone_else"
    assert c.follower_count is None
    for field in ("follower_count", "bio", "verified"):
        assert field in c.unavailable_fields


def test_a_zero_count_is_preserved_and_a_missing_one_is_not_invented():
    item = {**APIDOJO_ITEM, "shares": 0}
    del item["bookmarks"]
    p = apify.publication_from_apidojo(item)
    assert p.shares == 0, "a real zero was discarded"
    assert p.saves is None, "a missing field was invented as 0"


# ---------------------------------------------------------------------------
# actor selection encodes the Phase 0A finding
# ---------------------------------------------------------------------------

def test_hashtag_and_profile_use_different_instagram_actors():
    """Hashtags discover creators; profiles reach the back-catalogue."""
    hashtag = DiscoveryQuery(kind=QueryKind.HASHTAG,
                             platform=Platform.INSTAGRAM, value="piano")
    creator = DiscoveryQuery(kind=QueryKind.CREATOR,
                             platform=Platform.INSTAGRAM, value="someone")
    assert apify.actor_for(hashtag) == apify.ACTOR_IG_HASHTAG
    assert apify.actor_for(creator) == apify.ACTOR_IG_PROFILE


def test_actor_input_uses_only_keys_seen_in_the_probe():
    q = DiscoveryQuery(kind=QueryKind.CREATOR, platform=Platform.INSTAGRAM,
                       value="someone", limit=25)
    payload = apify.build_input(q, apify.ACTOR_IG_PROFILE, 25)
    assert payload == {"username": ["someone"], "resultsLimit": 25}

    q2 = DiscoveryQuery(kind=QueryKind.HASHTAG, platform=Platform.TIKTOK,
                        value="#pianist", limit=10)
    payload2 = apify.build_input(q2, apify.ACTOR_TIKTOK_EXACT, 10)
    assert payload2["startUrls"] == ["https://www.tiktok.com/tag/pianist"]
    assert payload2["maxItems"] == 10


# ---------------------------------------------------------------------------
# cost estimation errs high
# ---------------------------------------------------------------------------

def test_estimates_are_above_the_measured_rate():
    """An estimate below the real charge is how a cap is exceeded while
    every individual check passes, so the estimate carries a safety
    multiplier."""
    a = apify.ApifyDiscoveryAdapter("token", client=object())
    q = DiscoveryQuery(kind=QueryKind.CREATOR, platform=Platform.TIKTOK,
                       value="someone", limit=1000)
    measured = (apify.COST_PER_1K_USD[apify.ACTOR_TIKTOK_EXACT]
                * min(1000, apify.MAX_ITEMS_HARD_CAP) / 1000.0)
    assert a.estimate_usd(q) > measured


def test_a_tiny_query_still_reserves_something():
    a = apify.ApifyDiscoveryAdapter("token", client=object())
    q = DiscoveryQuery(kind=QueryKind.CREATOR, platform=Platform.TIKTOK,
                       value="someone", limit=1)
    assert a.estimate_usd(q) >= apify.MIN_ESTIMATE_USD


def test_the_item_cap_is_enforced_whatever_is_asked_for():
    a = apify.ApifyDiscoveryAdapter("token", client=object())
    q = DiscoveryQuery(kind=QueryKind.CREATOR, platform=Platform.TIKTOK,
                       value="someone", limit=10_000)
    assert apify.limit_of(q) == apify.MAX_ITEMS_HARD_CAP


def test_fixture_mode_costs_nothing():
    a = FixtureDiscoveryAdapter()
    q = DiscoveryQuery(kind=QueryKind.CREATOR, platform=Platform.TIKTOK,
                       value="demo_pianist", limit=200)
    assert a.estimate_usd(q) == 0.0
    assert a.is_fixture is True


# ---------------------------------------------------------------------------
# HTTP behaviour: retries, rate limits, bounded runs
# ---------------------------------------------------------------------------

def _client(handler) -> apify.ApifyClient:
    return apify.ApifyClient(
        "token", client=httpx.Client(transport=httpx.MockTransport(handler)),
        poll_seconds=0.0, run_timeout_seconds=5)


def test_a_rate_limit_is_retried(monkeypatch):
    monkeypatch.setattr(apify.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(200, json={"data": {"id": "run1"}})

    run = _client(handler).run_actor("a/b", {})
    assert run["id"] == "run1"
    assert calls["n"] == 3


def test_a_client_error_is_permanent_and_not_retried(monkeypatch):
    monkeypatch.setattr(apify.time, "sleep",
                        lambda s: pytest.fail("retried a 4xx"))

    def handler(request):
        return httpx.Response(404, json={"error": "no such actor"})

    with pytest.raises(DiscoveryError) as exc:
        _client(handler).run_actor("a/b", {})
    assert exc.value.permanent is True


def test_a_failed_run_raises_rather_than_returning_nothing():
    def handler(request):
        return httpx.Response(200, json={"data": {"id": "r", "status":
                                                  "FAILED"}})
    with pytest.raises(DiscoveryError, match="FAILED"):
        _client(handler).wait("r")


def test_a_run_that_never_finishes_is_aborted(monkeypatch):
    """A run left going keeps billing, so the timeout aborts rather than
    just giving up locally."""
    monkeypatch.setattr(apify.time, "sleep", lambda s: None)
    aborted = {"n": 0}

    def handler(request):
        if request.url.path.endswith("/abort"):
            aborted["n"] += 1
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"data": {"id": "r",
                                                  "status": "RUNNING"}})

    client = apify.ApifyClient(
        "token", client=httpx.Client(transport=httpx.MockTransport(handler)),
        poll_seconds=0.0, run_timeout_seconds=0)
    with pytest.raises(DiscoveryError, match="aborted"):
        client.wait("r")
    assert aborted["n"] == 1


def test_cost_comes_from_the_run_object():
    assert apify.ApifyClient.run_cost_usd({"usageTotalUsd": 0.3056}) == \
        pytest.approx(0.3056)
    assert apify.ApifyClient.run_cost_usd({"costUsd": 1.5}) == 1.5
    # No cost reported is None, never 0 - the difference decides whether a
    # reservation may be settled.
    assert apify.ApifyClient.run_cost_usd({"status": "SUCCEEDED"}) is None


def test_no_token_is_refused_before_any_request():
    with pytest.raises(DiscoveryError) as exc:
        apify.ApifyClient("")
    assert exc.value.permanent is True


# ---------------------------------------------------------------------------
# fixtures go through the live normalisation path
# ---------------------------------------------------------------------------

def test_fixture_results_are_labelled_everywhere():
    a = FixtureDiscoveryAdapter()
    r = a.search(DiscoveryQuery(kind=QueryKind.CREATOR,
                                platform=Platform.TIKTOK,
                                value="demo_pianist", limit=200))
    assert r.is_fixture is True
    assert r.provenance()["is_fixture"] is True
    assert any("FIXTURE" in n for n in r.coverage_notes)


def test_fixture_capabilities_are_not_softened():
    """A demo that pretended Instagram reports followers would hide the
    limitation the interface exists to communicate."""
    caps = FixtureDiscoveryAdapter().capabilities(Platform.INSTAGRAM)
    assert caps.follower_count is False


def test_provider_item_errors_are_surfaced_not_dropped():
    """The Instagram actor reports an inaccessible account as an item with
    `error`. Dropping it would turn a private account into an empty one."""
    a = FixtureDiscoveryAdapter()
    r = a.search(DiscoveryQuery(kind=QueryKind.CREATOR,
                                platform=Platform.INSTAGRAM,
                                value="demo_pianist_ig", limit=200))
    assert r.item_errors, "a provider-reported failure was swallowed"
    assert r.item_errors[0]["error"] == "not_found_or_private"


def test_fixture_incremental_filter_respects_since():
    a = FixtureDiscoveryAdapter()
    full = a.search(DiscoveryQuery(kind=QueryKind.CREATOR,
                                   platform=Platform.TIKTOK,
                                   value="demo_pianist", limit=500))
    cutoff = sorted(p.published_at for p in full.publications)[-5]
    partial = a.search(DiscoveryQuery(
        kind=QueryKind.CREATOR, platform=Platform.TIKTOK,
        value="demo_pianist", limit=500, since=cutoff))
    assert 0 < len(partial.publications) < len(full.publications)
    assert all(p.published_at > cutoff for p in partial.publications)


def test_an_unknown_fixture_fails_clearly():
    a = FixtureDiscoveryAdapter()
    with pytest.raises(DiscoveryError) as exc:
        a.search(DiscoveryQuery(kind=QueryKind.CREATOR,
                                platform=Platform.TIKTOK,
                                value="nobody_here"))
    assert exc.value.permanent is True
    msg = str(exc.value)
    # The message is for whoever is looking at the interface: it must name
    # the cause (no authorised provider) and the remedy, not just the fact.
    assert "No paid provider is authorised" in msg
    assert "config/settings.yaml" in msg
    assert "demo_pianist" in msg, "it should still say what IS available"


def test_adapter_selection_defaults_to_fixture():
    """So a request cannot accidentally reach a paid endpoint."""
    assert adapter_for("fixture").is_fixture is True
    assert adapter_for("").is_fixture is True
    with pytest.raises(DiscoveryError):
        adapter_for("apify", token=None)
    with pytest.raises(DiscoveryError):
        adapter_for("some-other-service")


def test_published_and_observed_times_are_separate():
    """They are different facts, and conflating them is what makes
    "what worked then versus now" unanswerable later."""
    a = FixtureDiscoveryAdapter()
    r = a.search(DiscoveryQuery(kind=QueryKind.CREATOR,
                                platform=Platform.TIKTOK,
                                value="demo_pianist", limit=5))
    for p in r.publications:
        assert p.published_at is not None
        assert p.published_at < r.observed_at
    assert r.observed_at > datetime.now(timezone.utc) - timedelta(minutes=5)


# ---------------------------------------------------------------------------
# credential handling
#
# `[PRIMARY]` https://docs.apify.com/api/v2 (checked 2026-10-25, API v2)
# supports both header and query-parameter auth and recommends the header:
# "URLs are often stored in browser history and server logs."
# ---------------------------------------------------------------------------

def test_the_token_never_appears_in_a_request_url():
    """It used to be sent as `?token=...` on every request."""
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["query"] = dict(request.url.params)
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"data": {"id": "r",
                                                  "status": "SUCCEEDED"}})

    _client(handler)._request("GET", "actor-runs/r")

    assert "SECRET-TOKEN" not in seen["url"]
    assert "token" not in seen["query"]
    assert "token" not in seen["url"]


def test_the_token_is_sent_as_a_bearer_header():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("Authorization")
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"data": {}})

    apify.ApifyClient(
        "SECRET-TOKEN",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        poll_seconds=0.0, run_timeout_seconds=5
    )._request("GET", "actor-runs/r")

    assert seen["auth"] == "Bearer SECRET-TOKEN"
    assert "SECRET-TOKEN" not in seen["url"]


def test_a_retry_log_line_cannot_carry_the_token(monkeypatch, caplog):
    """The retry path logs method and path. It must never log the full URL."""
    monkeypatch.setattr(apify.time, "sleep", lambda s: None)

    def handler(request):
        return httpx.Response(500, json={"error": "boom"})

    client = apify.ApifyClient(
        "SECRET-TOKEN",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        poll_seconds=0.0, run_timeout_seconds=5)
    with caplog.at_level("WARNING"):
        with pytest.raises(DiscoveryError):
            client._request("GET", "actor-runs/r")
    assert caplog.records, "expected retry warnings"
    assert "SECRET-TOKEN" not in caplog.text


def test_the_clockworks_parser_keeps_the_cover_url():
    """The provider hands over one frame free; we were dropping it.

    Both parsers in `apify.py` read the same fixture shape, and only
    one of them took `videoMeta.coverUrl`. The clockworks parser -
    the one that collected every competitor publication in the
    database - read `videoMeta` for duration and walked past the
    cover, so 3,388 publications have no visual signal of any kind
    when the provider was supplying one on every item.
    """
    import json
    import pathlib

    from server.discovery import apify

    raw = json.loads((pathlib.Path(__file__).parents[1] / "fixtures"
                      / "discovery" / "tiktok_hashtag_pianotok.json")
                     .read_text(encoding="utf-8"))
    items = raw if isinstance(raw, list) else raw.get("items", [])
    parsed = [apify.publication_from_clockworks(i) for i in items]
    parsed = [p for p in parsed if p is not None]
    assert parsed, "fixture produced no publications"
    with_cover = [p for p in parsed if p.thumbnail_url]
    assert len(with_cover) == len(parsed), (
        "every fixture item carries videoMeta.coverUrl")
    assert all(p.thumbnail_url.startswith("http") for p in with_cover)
