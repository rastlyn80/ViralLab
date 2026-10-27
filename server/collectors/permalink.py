"""The public URL of a publication. One derivation, used everywhere.

Clicking a figure in a report and landing on the actual post is the
difference between a number you have to trust and one you can check.
That link did not exist: nothing stored an Instagram permalink and
only the competitor downloader knew how to build a TikTok one.

## The two platforms are not symmetrical, and that decides the design

**TikTok is derivable.** A canonical URL is fully determined by the
handle and the publication id, so it is rebuilt on demand rather than
denormalised into a column that could go stale when a creator renames
themselves.

**Instagram is not.** A permalink carries a *shortcode*
(`/reel/DAbC_xyz/`), which is not the media id and cannot be computed
from it — there is no public mapping in either direction. It has to
come from the API and be kept. `instagram_official.MEDIA_FIELDS` has
requested `permalink` from the first version of this project and the
value was then dropped on the floor, which is why the column is new
and the backfill is free: the field was always in the response.

So `videos.permalink` is authoritative where present, and the TikTok
form is computed where it is not. Never the other way round: a stored
permalink is what the platform itself said, and a derived one is our
best reconstruction.

## What this does not do

It does not verify that a URL resolves. A deleted post keeps its
permalink, and checking every link on every render would mean
hundreds of outbound requests to two platforms that rate-limit. A
link here means "this is where that publication was", not "this is
live".
"""

from __future__ import annotations

import re

from server.db.models import Creator, Video

#: What a real TikTok username looks like: 2-24 characters of letters,
#: digits, underscore and period. No hyphens.
#:
#: MEASURED 2026-10-27, and it matters. The owner's TikTok creator row
#: carries `-000lRCwi-AFr6BTkIsmkCkFPfgeI8Izqw5p` as its handle - that
#: is the API's `open_id`, not a username, because the Display API
#: never returned a username for this account. Building a URL from it
#: produces a 404 for all 235 of the owner's TikTok publications, and
#: a link that 404s is worse than no link: the reader concludes the
#: post was deleted rather than that we never learned the handle.
TIKTOK_USERNAME = re.compile(r"^[A-Za-z0-9_.]{2,24}$")

#: Where a derived URL cannot be built, and why. Returned rather than
#: raising: a missing link is a normal state for a publication whose
#: handle we never learned, and it should render as "no link" instead
#: of breaking the page around it.
NO_HANDLE = "the creator's handle is not known, so no URL can be built"
NOT_A_USERNAME = (
    "the stored handle is the platform's internal account id rather "
    "than a username - the Display API never returned one for this "
    "account - and a URL built from it would not resolve")
NO_EXTERNAL_ID = "the publication has no platform id"
UNSUPPORTED = "no URL form is known for this platform"


def tiktok_url(handle: str, external_id: str) -> str:
    return f"https://www.tiktok.com/@{handle}/video/{external_id}"


def for_video(video: Video, creator: Creator | None) -> str | None:
    """The best public URL for one publication, or None.

    `video.permalink` wins wherever it exists: it is what the platform
    said, and anything computed here is a reconstruction of it.
    """
    stored = getattr(video, "permalink", None)
    if stored:
        return stored
    if not video.external_id:
        return None
    if video.platform == "tiktok":
        handle = (creator.handle if creator else None)
        if not handle or not TIKTOK_USERNAME.match(handle):
            return None
        return tiktok_url(handle, video.external_id)
    # Instagram and anything else: no derivation exists. A guessed URL
    # that 404s is worse than an honest absence, because the reader
    # concludes the post is gone rather than that we never stored it.
    return None


def why_missing(video: Video, creator: Creator | None) -> str | None:
    """A sentence explaining an absent link, for the interface."""
    if for_video(video, creator):
        return None
    if not video.external_id:
        return NO_EXTERNAL_ID
    if video.platform == "tiktok":
        handle = creator.handle if creator else None
        if not handle:
            return NO_HANDLE
        if not TIKTOK_USERNAME.match(handle):
            return NOT_A_USERNAME
        return None
    if video.platform == "instagram":
        return ("Instagram permalinks carry a shortcode that cannot be "
                "computed from the media id, and none was stored for "
                "this publication. Re-running the Instagram collector "
                "fills it in - the field is already in the response.")
    return UNSUPPORTED


def bulk(session, video_ids: list[int]) -> dict[int, str]:
    """URLs for many publications in two queries rather than 2N.

    The report links dozens of publications per section; a per-row
    lookup would turn one page into a hundred round trips.
    """
    from sqlalchemy import select

    if not video_ids:
        return {}
    rows = session.execute(
        select(Video, Creator)
        .join(Creator, Creator.id == Video.creator_id)
        .where(Video.id.in_(list(video_ids)))).all()
    out: dict[int, str] = {}
    for video, creator in rows:
        url = for_video(video, creator)
        if url:
            out[video.id] = url
    return out
