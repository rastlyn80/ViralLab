"""Is TikTok competitor collection possible at all right now? Runs ON Ubuntu.

Throwaway (CLAUDE.md §1.2).

`apidojo/tiktok-scraper`, the actor `actor_for()` hardcodes for every
TikTok query, currently returns its `{"noResults": true}` sentinel for
both creator and hashtag queries and bills $0.0030 a run regardless.
Verified 2026-10-26 against Apify's own API: runs SUCCEEDED, exit code 0,
dataset itemCount 10, every item the sentinel.

That makes the paid-plan question unanswerable as posed — a subscription
does not fix an actor that returns nothing. This probe asks the prior
question: does the OTHER actor validated in Phase 0A still work?

`clockworks/free-tiktok-scraper` costs $3.00/1000 against $0.30/1000 and
rounds large counts to ~3 significant figures, which blurs outlier
boundaries (CAPS_TIKTOK). It is the fallback, not the preference. But a
rounded figure that exists beats an exact one that does not.

Goes through `discover()` so the budget reservation, the advisory lock
and the settle discipline all apply — the actor choice is the only thing
patched.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, "/home/rastlyn/virallab")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from server.discovery import apify as apify_mod  # noqa: E402
from server.discovery.apify import (  # noqa: E402
    ACTOR_TIKTOK_ROUNDED, ApifyDiscoveryAdapter,
)
from server.research import discover as discover_mod  # noqa: E402
from server.research.discover import discover  # noqa: E402

LIMIT = int(os.environ.get("LIMIT", "20"))
VALUE = os.environ.get("VALUE", "classicalpiano")


def main() -> int:
    original = apify_mod.actor_for

    def forced(query):
        if query.platform is apify_mod.Platform.TIKTOK:
            return ACTOR_TIKTOK_ROUNDED
        return original(query)

    # Patch where it is LOOKED UP, not only where it is defined: the
    # adapter may hold its own reference.
    apify_mod.actor_for = forced
    for mod in (discover_mod,):
        if getattr(mod, "actor_for", None) is original:
            mod.actor_for = forced

    eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    S = sessionmaker(eng, expire_on_commit=False, future=True)
    with S() as s:
        out = discover(s, ApifyDiscoveryAdapter(os.environ["APIFY_TOKEN"]),
                       kind="hashtag", platform="tiktok", value=VALUE,
                       limit=LIMIT, force=True)
        s.commit()
        print(json.dumps({
            "actor": ACTOR_TIKTOK_ROUNDED,
            "value": VALUE,
            "asked_for": LIMIT,
            "status": out.status,
            "creators_seen": out.creators_seen,
            "publications_seen": out.publications_seen,
            "estimated_usd": out.estimated_usd,
            "billed_usd": out.actual_usd,
            "message": out.message,
        }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
