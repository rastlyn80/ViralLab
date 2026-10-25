"""The DiscoveryAdapter seam.

Competitor data cannot come from official APIs - `[PRIMARY, ADR 0001]`
TikTok's Display API covers only the authorised account, and Instagram's
Graph API exposes nothing about other creators. So discovery means a
third-party provider, under the operating policy in `DEVELOPMENT_PLAN.md`
§2.3, and that provider sits behind this interface so the rest of the system
never depends on one vendor's field names.

Two ideas carry most of the weight here.

**Capabilities are declared, not discovered by failing.** A provider states
up front what it can and cannot return for a platform. The validated
Instagram actors return publications with an owner username but **no
follower count and no bio** - verified against recorded live responses, not
assumed - while the TikTok actors do return followers. Code that wants a
follower count asks the capability first and records `unavailable` rather
than writing a zero. That is the difference between "this creator has 0
followers" and "this source does not report followers", and the product
vision forbids conflating them.

**Everything carries provenance and two timestamps.** `published_at` is when
the creator posted; `observed_at` is when we looked. They are different
facts, stored separately, and never overwritten - which is what makes the
eventual "what worked then versus now" question answerable at all.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Platform(str, Enum):
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"


class QueryKind(str, Enum):
    #: One account, by username or profile URL. The back-catalogue route.
    CREATOR = "creator"
    #: Free-text search. Discovers creators; see the warning below.
    KEYWORD = "keyword"
    HASHTAG = "hashtag"


class DiscoveryError(RuntimeError):
    """The provider could not serve this request.

    Transient by default. `permanent=True` marks the ones where retrying
    cannot help - a bad handle, an unsupported query kind - so a caller does
    not burn budget rediscovering the same refusal.
    """

    def __init__(self, message: str, *, permanent: bool = False,
                 provider: str | None = None):
        super().__init__(message)
        self.permanent = permanent
        self.provider = provider


@dataclass(frozen=True)
class ProviderCapabilities:
    """What a provider actually returns, per platform.

    Declaring absence is the point. `follower_count=False` for Instagram is
    an observed fact about the validated actors, and it propagates into the
    database as `unavailable` instead of a fabricated number.
    """

    provider: str
    platform: Platform
    supported_queries: tuple[QueryKind, ...]
    #: Creator-level fields
    follower_count: bool = False
    creator_bio: bool = False
    creator_verified: bool = False
    #: Publication-level fields
    view_count: bool = True
    #: True when the source rounds large figures, which blurs outlier
    #: boundaries and must travel with the data.
    views_rounded: bool = False
    saves_count: bool = False
    shares_count: bool = False
    music_metadata: bool = False
    media_url: bool = False
    #: Whether the source can reach a full back-catalogue, or only recent
    #: content. `[PRIMARY, measured 0A]` hashtag feeds returned 0 of 45
    #: records aged >=35 days, so they cannot build a mature fixed-age
    #: corpus and must be used to find creators, never to measure them.
    full_history: bool = False
    #: Free-text notes that end up in stored provenance.
    coverage_notes: tuple[str, ...] = ()

    def missing_creator_fields(self) -> tuple[str, ...]:
        out = []
        if not self.follower_count:
            out.append("follower_count")
        if not self.creator_bio:
            out.append("bio")
        if not self.creator_verified:
            out.append("verified")
        return tuple(out)


@dataclass(frozen=True)
class DiscoveryQuery:
    kind: QueryKind
    platform: Platform
    #: A username, a profile URL, a keyword or a hashtag, depending on kind.
    value: str
    limit: int = 50
    #: Opaque provider cursor for incremental collection.
    cursor: str | None = None
    #: Only fetch publications newer than this, where the provider supports
    #: it. Incremental refresh uses it to avoid re-paying for known rows.
    since: datetime | None = None

    def normalised_value(self) -> str:
        v = self.value.strip()
        if self.kind is QueryKind.CREATOR:
            return normalise_handle(v)
        if self.kind is QueryKind.HASHTAG:
            return v.lstrip("#").lower()
        return v

    def cache_key(self) -> str:
        """Stable identity for this query, for idempotency and dedup."""
        since = self.since.date().isoformat() if self.since else "-"
        return (f"{self.platform.value}:{self.kind.value}:"
                f"{self.normalised_value()}:{self.limit}:{since}:"
                f"{self.cursor or '-'}")


_HANDLE_RE = re.compile(r"^[A-Za-z0-9._]{1,64}$")


def normalise_handle(value: str) -> str:
    """A bare lowercase username, from a handle or a profile URL.

    Accepts what a person would actually paste: `@tonyann`,
    `tiktok.com/@tonyann`, `instagram.com/tonyann/`, with or without a
    scheme, query string or trailing slash.
    """
    v = value.strip()
    if not v:
        raise ValueError("empty handle")
    if "/" in v or v.count(".") > 1:
        # A URL. Take the FIRST meaningful segment, not the last: a post URL
        # like tiktok.com/@someone/video/123 ends in the video id, and
        # instagram.com/someone/reel/xyz ends in the shortcode. Either would
        # otherwise be mistaken for the handle.
        v = v.split("?")[0].split("#")[0].strip("/")
        parts = [p for p in v.split("/") if p]
        noise = {"reel", "reels", "p", "video", "tv", "www", "https:",
                 "http:", "tiktok.com", "www.tiktok.com", "instagram.com",
                 "www.instagram.com", "vm.tiktok.com", "explore", "tags"}
        parts = [p for p in parts if p.lower() not in noise]
        # An @-prefixed segment is unambiguous wherever it appears.
        at = next((p for p in parts if p.startswith("@")), None)
        if at:
            v = at
        elif parts:
            v = parts[0]
        else:
            raise ValueError(f"{value!r} contains no username")
    v = v.lstrip("@").strip().lower()
    if not _HANDLE_RE.match(v):
        raise ValueError(f"{value!r} does not look like a username or "
                         f"profile URL")
    return v


@dataclass
class DiscoveredCreator:
    """One account as a provider reported it.

    Absent fields are `None` and listed in `unavailable_fields`, so a reader
    can tell "the source does not report this" from "the value is zero".
    """

    platform: Platform
    username: str
    external_id: str | None = None
    display_name: str | None = None
    profile_url: str | None = None
    bio: str | None = None
    #: Links in the bio. The strongest available evidence for tying a TikTok
    #: account to an Instagram one, which is why it is extracted rather than
    #: left inside the raw blob.
    bio_links: list[str] = field(default_factory=list)
    follower_count: int | None = None
    following_count: int | None = None
    publication_count: int | None = None
    total_likes: int | None = None
    verified: bool | None = None
    #: True when the provider rounds large figures.
    counts_rounded: bool = False
    unavailable_fields: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


@dataclass
class DiscoveredPublication:
    """One publication as a provider reported it."""

    platform: Platform
    external_id: str
    creator_username: str
    #: When the creator posted it. Distinct from observed_at, always.
    published_at: datetime | None = None
    caption: str | None = None
    hashtags: list[str] = field(default_factory=list)
    duration_s: float | None = None
    url: str | None = None
    thumbnail_url: str | None = None
    #: A direct media URL where the provider supplies one. Having it is not
    #: permission to download it - see docs/MULTIMODAL_PIPELINE.md.
    media_url: str | None = None
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    saves: int | None = None
    #: True when the source rounded these figures to ~3 significant digits,
    #: which blurs outlier boundaries.
    counts_rounded: bool = False
    music_title: str | None = None
    music_artist: str | None = None
    music_id: str | None = None
    music_is_original: bool | None = None
    unavailable_fields: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


@dataclass
class DiscoveryResult:
    """Everything one provider call produced, including what it could not."""

    provider: str
    query: DiscoveryQuery
    #: When we looked. Never confused with when anything was published.
    observed_at: datetime
    creators: list[DiscoveredCreator] = field(default_factory=list)
    publications: list[DiscoveredPublication] = field(default_factory=list)
    next_cursor: str | None = None
    #: Costs. The estimate is what the budget reserved; the actual is what
    #: the provider billed, and only the actual may be reported as cost
    #: (CLAUDE.md 5.3).
    estimated_usd: float | None = None
    actual_usd: float | None = None
    #: Per-item failures the provider reported, kept rather than dropped -
    #: the Instagram reel actor returns `error`/`errorDescription` for
    #: inaccessible accounts, and silently discarding those would turn a
    #: private account into an apparently empty one.
    item_errors: list[dict] = field(default_factory=list)
    #: Known limits of this result, copied into stored provenance.
    coverage_notes: list[str] = field(default_factory=list)
    capabilities: ProviderCapabilities | None = None
    #: True when this came from committed fixtures rather than a live call.
    #: Carried all the way to the interface so a demonstration can never be
    #: mistaken for collected data.
    is_fixture: bool = False
    truncated: bool = False

    def provenance(self) -> dict:
        """What gets stored next to every row this result produced."""
        caps = self.capabilities
        return {
            "provider": self.provider,
            "is_fixture": self.is_fixture,
            "observed_at": self.observed_at.isoformat(),
            "query": {"kind": self.query.kind.value,
                      "platform": self.query.platform.value,
                      "value": self.query.normalised_value(),
                      "limit": self.query.limit},
            "estimated_usd": self.estimated_usd,
            "actual_usd": self.actual_usd,
            "coverage_notes": list(self.coverage_notes),
            "item_error_count": len(self.item_errors),
            "truncated": self.truncated,
            "capabilities": None if caps is None else {
                "full_history": caps.full_history,
                "views_rounded": caps.views_rounded,
                "follower_count": caps.follower_count,
                "media_url": caps.media_url,
                "missing_creator_fields": list(
                    caps.missing_creator_fields()),
            },
        }


class DiscoveryAdapter(ABC):
    """One provider. Platform-specific field names stop here.

    Implementations must not invent a value they did not receive. A field the
    provider does not return is `None` plus an entry in
    `unavailable_fields`; a *failure* is a `DiscoveryError` or an entry in
    `item_errors`. Those are different and the difference is load-bearing.
    """

    name: str = "abstract"
    #: True for adapters that read committed fixtures and cost nothing.
    is_fixture: bool = False

    @abstractmethod
    def capabilities(self, platform: Platform) -> ProviderCapabilities:
        """What this provider returns for that platform."""

    @abstractmethod
    def estimate_usd(self, query: DiscoveryQuery) -> float:
        """Cost to run this query, before running it.

        Used to reserve budget, so it must err high rather than low: an
        estimate below the real charge is how a cap is exceeded while every
        individual check passes.
        """

    @abstractmethod
    def search(self, query: DiscoveryQuery) -> DiscoveryResult:
        """Run one query. Raises DiscoveryError on provider failure."""

    def supports(self, query: DiscoveryQuery) -> bool:
        caps = self.capabilities(query.platform)
        return query.kind in caps.supported_queries
