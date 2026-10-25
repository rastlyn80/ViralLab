"""Generate synthetic discovery fixtures shaped like real actor output.

Why synthetic rather than the Phase 0A recordings: those contain real
third-party creators' data and are gitignored under the operating policy
(DEVELOPMENT_PLAN.md 2.3 - internal research use only, no redistributable
creator database). Committing them would redistribute personal data.

So these are **fabricated**, and deliberately obviously so: handles are
`demo_*`, display names say "(fixture)". The *shape* is copied field-for-
field from the recorded responses, so the normalisers are exercised against
the real contract without redistributing anyone's data.

The trajectory is constructed so the breakthrough detector has something
genuine to find: a long low-performing run, one step change, then a
sustained higher level. The detector is not told where it is.

    python scripts/make_discovery_fixtures.py
"""

from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "discovery"

#: Fictional. Any resemblance to a real account is unintended.
TIKTOK_HANDLE = "demo_pianist"
TIKTOK_NAME = "Demo Pianist (fixture)"
IG_HANDLE = "demo_pianist_ig"
IG_NAME = "Demo Pianist (fixture)"

START = datetime(2024, 3, 1, 18, 30, tzinfo=timezone.utc)
#: Index at which the creator changes what they make. The detector must find
#: this from the data, so it is not recorded in the fixture.
BREAKTHROUGH_AT = 34
N_POSTS = 62

REPERTOIRE = [
    ("Chopin", "Nocturne in E-flat"), ("Chopin", "Ballade No. 1"),
    ("Debussy", "Clair de Lune"), ("Satie", "Gymnopedie No. 1"),
    ("Rachmaninoff", "Prelude in G minor"), ("Liszt", "Liebestraum"),
    ("Bach", "Prelude in C"), ("Beethoven", "Moonlight, 1st movement"),
    ("Ravel", "Pavane"), ("Einaudi", "Nuvole Bianche"),
]
HOOKS = [
    "The saddest chord in classical music",
    "I played this at 6am and could not stop",
    "POV: you hear this for the first time",
    "This took me 11 years to learn",
    "Nobody talks about this passage",
]


def views_for(i: int, rng: random.Random) -> int:
    """A baseline, one step change, and noise.

    Lognormal-ish: social performance is multiplicative, so noise is applied
    as a factor rather than an offset. Before the step the creator sits
    around 4k; after, around 120k, with occasional outliers either side so
    the detector cannot succeed by finding a single maximum.
    """
    base = 4_000 if i < BREAKTHROUGH_AT else 120_000
    # A couple of pre-breakthrough flukes, so "highest view count" is not
    # the same as "where the trajectory changed".
    if i in (12, 23):
        base *= 6
    noise = math.exp(rng.gauss(0, 0.45))
    return int(base * noise)


def tiktok_items() -> list[dict]:
    """Shaped like apidojo/tiktok-scraper output."""
    rng = random.Random(20261025)
    items = []
    for i in range(N_POSTS):
        posted = START + timedelta(days=i * 6, hours=rng.randint(0, 9))
        views = views_for(i, rng)
        composer, work = REPERTOIRE[i % len(REPERTOIRE)]
        hook = HOOKS[i % len(HOOKS)] if i >= BREAKTHROUGH_AT else work
        likes = int(views * rng.uniform(0.06, 0.14))
        items.append({
            "inputSource": "profile",
            "id": f"74{i:016d}",
            "title": f"{hook} #{composer.lower()} #piano #classicalmusic",
            "views": views,
            "likes": likes,
            "comments": int(likes * rng.uniform(0.01, 0.05)),
            "shares": int(likes * rng.uniform(0.02, 0.09)),
            "bookmarks": int(likes * rng.uniform(0.05, 0.25)),
            "hashtags": [composer.lower(), "piano", "classicalmusic"],
            "channel": {
                "id": "7000000000000000001",
                "name": TIKTOK_NAME,
                "username": TIKTOK_HANDLE,
                "avatar": "https://example.invalid/avatar.jpg",
                "verified": False,
                "url": f"https://www.tiktok.com/@{TIKTOK_HANDLE}",
                # One figure, observed now - deliberately NOT a history.
                "followers": 184_300,
                "following": 212,
                "videos": N_POSTS,
                "likes": 2_940_000,
            },
            "collabInfo": None,
            "uploadedAt": int(posted.timestamp()),
            "uploadedAtFormatted": posted.strftime("%Y-%m-%d %H:%M:%S"),
            "video": {
                "width": 1080, "height": 1920, "ratio": "9:16",
                "duration": round(rng.uniform(14, 58), 2),
                "url": f"https://example.invalid/v/74{i:016d}.mp4",
                "cover": f"https://example.invalid/c/74{i:016d}.jpg",
                "thumbnail": f"https://example.invalid/t/74{i:016d}.jpg",
            },
            "song": {
                "id": f"69{i:016d}",
                "title": f"{work} - {composer}",
                "artist": TIKTOK_NAME,
                "album": "",
                "duration": round(rng.uniform(20, 70), 1),
                "cover": "https://example.invalid/s.jpg",
            },
            "subtitleInformation": None,
            "postPage": f"https://www.tiktok.com/@{TIKTOK_HANDLE}/video/"
                        f"74{i:016d}",
            "poi": None,
        })
    return items


def instagram_items() -> list[dict]:
    """Shaped like apify/instagram-reel-scraper output.

    Note what is absent, matching the real actor: no follower count, no bio,
    and `videoViewCount` null with `videoPlayCount` carrying the figure.
    """
    rng = random.Random(7731)
    items = []
    for i in range(N_POSTS - 8):
        posted = START + timedelta(days=i * 6 + 1, hours=rng.randint(0, 9))
        views = int(views_for(i, rng) * rng.uniform(0.35, 0.8))
        composer, work = REPERTOIRE[i % len(REPERTOIRE)]
        likes = int(views * rng.uniform(0.05, 0.12))
        items.append({
            "inputUrl": f"https://www.instagram.com/{IG_HANDLE}/",
            "id": f"31{i:015d}",
            "type": "Video",
            "shortCode": f"Dq{i:05d}",
            "caption": f"{work} - {composer}. #piano #classicalmusic",
            "hashtags": ["piano", "classicalmusic", composer.lower()],
            "mentions": [],
            "url": f"https://www.instagram.com/reel/Dq{i:05d}/",
            "commentsCount": int(likes * rng.uniform(0.01, 0.04)),
            "firstComment": "beautiful",
            "latestComments": [],
            "dimensionsHeight": 1920, "dimensionsWidth": 1080,
            "displayUrl": f"https://example.invalid/ig/{i}.jpg",
            "images": [],
            "videoUrl": f"https://example.invalid/ig/{i}.mp4",
            "likesCount": likes,
            "videoPlayCount": views,
            "videoViewCount": None,
            "timestamp": posted.isoformat().replace("+00:00", "Z"),
            "childPosts": [],
            "ownerFullName": IG_NAME,
            "ownerUsername": IG_HANDLE,
            "ownerId": "50000000001",
            "productType": "clips",
            "videoDuration": round(rng.uniform(14, 58), 3),
            "paidPartnership": False,
            "isPinned": False,
            "sharesCount": int(likes * rng.uniform(0.01, 0.06)),
            "isCommentsDisabled": False,
            "musicInfo": {
                "artist_name": IG_NAME,
                "song_name": f"{work} - {composer}",
                "uses_original_audio": True,
                "should_mute_audio": False,
                "should_mute_audio_reason": "",
                "audio_id": f"88{i:014d}",
            },
        })
    # The real actor reports an inaccessible account as an item carrying
    # `error`, and the adapter must surface rather than drop it.
    items.append({
        "url": "https://www.instagram.com/demo_private_account/",
        "username": "demo_private_account",
        "error": "not_found_or_private",
        "errorDescription": "The account is private or does not exist, so "
                            "no publications could be read.",
    })
    return items


def hashtag_items() -> list[dict]:
    """Shaped like apify/instagram-hashtag-scraper: several creators, all
    recent - which is why it discovers rather than measures."""
    rng = random.Random(991)
    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    out = []
    for i, handle in enumerate(["demo_pianist_ig", "demo_keys_daily",
                                "demo_nocturne", "demo_practice_room",
                                "demo_grandpiano"]):
        for j in range(3):
            posted = now - timedelta(days=rng.randint(0, 17))
            views = int(math.exp(rng.gauss(10.5, 1.3)))
            likes = int(views * rng.uniform(0.04, 0.13))
            idx = i * 10 + j
            out.append({
                "inputUrl": "https://www.instagram.com/explore/tags/"
                            "classicalpiano/",
                "id": f"32{idx:015d}",
                "type": "Video",
                "shortCode": f"Dh{idx:05d}",
                "caption": "practice clip #classicalpiano #piano",
                "hashtags": ["classicalpiano", "piano"],
                "mentions": [],
                "url": f"https://www.instagram.com/reel/Dh{idx:05d}/",
                "commentsCount": int(likes * 0.02),
                "firstComment": "",
                "latestComments": [],
                "dimensionsHeight": 1920, "dimensionsWidth": 1080,
                "displayUrl": f"https://example.invalid/h/{idx}.jpg",
                "images": [],
                "videoUrl": f"https://example.invalid/h/{idx}.mp4",
                "likesCount": likes,
                "videoPlayCount": views,
                "igPlayCount": views,
                "timestamp": posted.isoformat().replace("+00:00", "Z"),
                "childPosts": [],
                "ownerFullName": f"{handle} (fixture)",
                "ownerUsername": handle,
                "ownerId": f"5000000{i:04d}",
                "productType": "clips",
                "videoDuration": round(rng.uniform(10, 50), 3),
                "paidPartnership": False,
                "isPinned": False,
                "sharesCount": int(likes * 0.03),
                "musicInfo": {
                    "artist_name": "", "song_name": "original audio",
                    "uses_original_audio": True, "should_mute_audio": False,
                    "should_mute_audio_reason": "",
                    "audio_id": f"77{idx:014d}"},
            })
    return out


BANNER = {
    "_fixture": True,
    "_warning": "SYNTHETIC DATA. Fabricated for testing and demonstration. "
                "Not collected from any platform and not about any real "
                "person. Field shapes copy the Phase 0A recorded responses; "
                "values do not.",
}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    files = {
        "tiktok_creator_demo_pianist.json": {
            "actor": "apidojo/tiktok-scraper",
            "platform": "tiktok", "kind": "creator",
            "value": TIKTOK_HANDLE, "items": tiktok_items()},
        "instagram_creator_demo_pianist.json": {
            "actor": "apify/instagram-reel-scraper",
            "platform": "instagram", "kind": "creator",
            "value": IG_HANDLE, "items": instagram_items()},
        "instagram_hashtag_classicalpiano.json": {
            "actor": "apify/instagram-hashtag-scraper",
            "platform": "instagram", "kind": "hashtag",
            "value": "classicalpiano", "items": hashtag_items()},
    }
    for name, payload in files.items():
        (OUT / name).write_text(
            json.dumps({**BANNER, **payload}, indent=1), encoding="utf-8")
        print(f"  wrote {name}  ({len(payload['items'])} item(s))")
    print(f"\n  -> {OUT}")
    print("  Breakthrough is planted at index "
          f"{BREAKTHROUGH_AT} of {N_POSTS} and is NOT recorded in the "
          f"fixtures, so the detector has to find it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
