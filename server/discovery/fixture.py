"""Fixture-backed DiscoveryAdapter. Costs nothing, spends nothing.

This exists because no paid provider is authorised, and the whole research
workflow still has to be buildable, testable and demonstrable. It reads
committed synthetic fixtures and pushes them through **the live adapter's
own normalisers** - not a parallel implementation, which would prove nothing
about the code that will actually run.

Everything it returns carries `is_fixture=True`, which travels into the
database and all the way to the interface. A demonstration must never be
mistakable for collected data.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from server.discovery.apify import ApifyDiscoveryAdapter
from server.discovery.base import (DiscoveryError, DiscoveryQuery,
                                   DiscoveryResult, Platform,
                                   ProviderCapabilities, QueryKind)

logger = logging.getLogger("virallab.discovery.fixture")

FIXTURE_DIR = (Path(__file__).resolve().parent.parent.parent
               / "tests" / "fixtures" / "discovery")


class FixtureDiscoveryAdapter(ApifyDiscoveryAdapter):
    """Serves recorded fixtures through the real normalisation path."""

    name = "fixture"
    is_fixture = True

    def __init__(self, fixture_dir: Path | None = None):
        # Deliberately does NOT call super().__init__: there is no token and
        # no HTTP client, and constructing one would risk a live call.
        self.dir = Path(fixture_dir or FIXTURE_DIR)
        self.max_items_cap = 1000

    # -- discovery ---------------------------------------------------------

    def available(self) -> list[dict]:
        """What fixtures exist, for the interface to offer honestly."""
        out = []
        for p in sorted(self.dir.glob("*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            out.append({"file": p.name, "platform": d.get("platform"),
                        "kind": d.get("kind"), "value": d.get("value"),
                        "actor": d.get("actor"),
                        "items": len(d.get("items") or [])})
        return out

    def _load(self, query: DiscoveryQuery) -> dict:
        want_value = query.normalised_value()
        for p in sorted(self.dir.glob("*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if (d.get("platform") == query.platform.value
                    and d.get("kind") == query.kind.value
                    and str(d.get("value", "")).lower() == want_value):
                return d
        raise DiscoveryError(
            f"no fixture for {query.kind.value} {want_value!r} on "
            f"{query.platform.value}. Available: "
            f"{[(f['platform'], f['kind'], f['value']) for f in self.available()]}",
            permanent=True, provider=self.name)

    def capabilities(self, platform: Platform) -> ProviderCapabilities:
        """The provider's real capabilities, plus a fixture warning.

        The capability set is NOT softened for fixtures: Instagram still
        reports no follower count, because pretending otherwise in a demo
        would hide exactly the limitation the interface has to communicate.
        """
        caps = super().capabilities(platform)
        return ProviderCapabilities(
            provider=self.name, platform=caps.platform,
            supported_queries=caps.supported_queries,
            follower_count=caps.follower_count,
            creator_bio=caps.creator_bio,
            creator_verified=caps.creator_verified,
            view_count=caps.view_count, views_rounded=caps.views_rounded,
            saves_count=caps.saves_count, shares_count=caps.shares_count,
            music_metadata=caps.music_metadata, media_url=caps.media_url,
            full_history=caps.full_history,
            coverage_notes=caps.coverage_notes + (
                "FIXTURE DATA: synthetic, fabricated for demonstration. Not "
                "collected from any platform and not about any real person.",
            ))

    def estimate_usd(self, query: DiscoveryQuery) -> float:
        return 0.0

    def search(self, query: DiscoveryQuery) -> DiscoveryResult:
        payload = self._load(query)
        items = list(payload.get("items") or [])
        if query.since is not None:
            # Incremental refresh has to work in fixture mode too, or the
            # workflow it belongs to is untested.
            items = [i for i in items
                     if _published(i) is None or _published(i) > query.since]
        limit = min(max(1, query.limit), self.max_items_cap)
        items = items[:limit]

        result = self.normalise(
            query, payload["actor"], items,
            observed_at=datetime.now(timezone.utc),
            actual_usd=0.0, is_fixture=True)
        result.estimated_usd = 0.0
        result.provider = self.name
        logger.info("fixture discovery: %s %s -> %d creator(s), "
                    "%d publication(s), %d item error(s)",
                    query.kind.value, query.normalised_value(),
                    len(result.creators), len(result.publications),
                    len(result.item_errors))
        return result


def _published(item: dict) -> datetime | None:
    """Published time from either actor shape, for `since` filtering."""
    from server.discovery.apify import _epoch, _iso
    return (_iso(item.get("timestamp"))
            or _iso(item.get("createTimeISO"))
            or _epoch(item.get("uploadedAt"))
            or _epoch(item.get("createTime")))


def adapter_for(provider: str, *, token: str | None = None
                ) -> ApifyDiscoveryAdapter:
    """Pick an adapter by name.

    `fixture` is the default everywhere until a provider and a budget are
    authorised, so the research workflow cannot accidentally reach a paid
    endpoint.
    """
    if provider in ("fixture", "", None):
        return FixtureDiscoveryAdapter()
    if provider == "apify":
        if not token:
            raise DiscoveryError(
                "apify selected but no APIFY_TOKEN is configured",
                permanent=True, provider="apify")
        return ApifyDiscoveryAdapter(token)
    raise DiscoveryError(f"unknown discovery provider {provider!r}",
                         permanent=True)
