"""Fetch and hash TikTok cover images; backfill Instagram durations.

Runs ON Ubuntu. Two jobs, both filling gaps that block cross-platform
matching:

  1. **TikTok cover images.** The API returns no media file, so the cover is
     the only content signal TikTok offers. Its URL is a short-lived CDN link,
     so the image is fetched and reduced to a dHash immediately; the hash
     stays valid forever, the URL does not.

  2. **Instagram durations.** `videos.duration_s` was NULL for all 289
     Instagram rows - the Graph API response does not carry it - while the
     downloaded media files do. Duration is a cross-platform signal and a
     Phase 3 input, so it is backfilled from the fingerprint file rather than
     left missing.

The dHash is computed with ffmpeg rather than an imaging library, so the
server needs no new dependency: ffmpeg scales to 9x8 greyscale and the
comparison is the same one used for video frames.

    .venv/bin/python fetch_covers.py              # both jobs
    .venv/bin/python fetch_covers.py --covers-only
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/rastlyn/virallab")
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from server.collectors.tiktok_official import (  # noqa: E402
    TikTokAuth, TikTokOfficialAdapter)
from server.collectors.tokens import TokenStore  # noqa: E402
from server.db.models import OAuthToken, Video  # noqa: E402

FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
HASH_W, HASH_H = 9, 8
IG_FP = Path("/home/rastlyn/virallab/ig_fingerprints.json")


def dhash_bytes(data: bytes) -> str | None:
    """dHash of an encoded image, via ffmpeg. Same construction as the
    video-frame hash in probes/local_library/fingerprint.py."""
    try:
        r = subprocess.run(
            [FFMPEG, "-v", "error", "-i", "pipe:0",
             "-vf", f"scale={HASH_W}:{HASH_H}", "-pix_fmt", "gray",
             "-frames:v", "1", "-f", "rawvideo", "pipe:1"],
            input=data, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    px = r.stdout
    if len(px) < HASH_W * HASH_H:
        return None
    bits = 0
    for row in range(HASH_H):
        base = row * HASH_W
        for col in range(HASH_W - 1):
            bits = (bits << 1) | int(px[base + col] > px[base + col + 1])
    return f"{bits:016x}"


def fetch(url: str) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ViralLab"})
        with urllib.request.urlopen(req, timeout=60) as fh:
            return fh.read()
    except Exception:                                  # noqa: BLE001
        return None


def do_covers(s, adapter, account_ref: str) -> None:
    rows = {v.external_id: v for v in s.scalars(
        select(Video).where(Video.platform == "tiktok")).all()}
    print(f"  {len(rows)} TikTok publication(s) in the database")

    seen = fetched = failed = unchanged = 0
    now = datetime.now(timezone.utc)
    for raw in adapter._iter_raw(account_ref):
        ext = str(raw.get("id") or "")
        v = rows.get(ext)
        if v is None:
            continue
        seen += 1
        url = raw.get("cover_image_url")
        if not url:
            continue
        if v.cover_hash:
            # Already hashed. The URL is refreshed anyway, since the stored
            # one has almost certainly expired.
            v.cover_url, v.cover_fetched_at = url, now
            unchanged += 1
            continue
        data = fetch(url)
        h = dhash_bytes(data) if data else None
        if h is None:
            failed += 1
            continue
        v.cover_url, v.cover_hash, v.cover_fetched_at = url, h, now
        fetched += 1
        if fetched % 25 == 0:
            s.commit()
            print(f"    hashed {fetched}...", flush=True)
    s.commit()
    print(f"  covers: {fetched} newly hashed, {unchanged} already had one, "
          f"{failed} failed, {seen} matched a stored publication")
    n = sum(1 for v in rows.values() if v.cover_hash)
    print(f"  {n}/{len(rows)} TikTok publications now have a cover hash")


def do_ig_durations(s) -> None:
    if not IG_FP.exists():
        print("  no ig_fingerprints.json; skipping duration backfill")
        return
    fps = json.loads(IG_FP.read_text(encoding="utf-8"))
    rows = s.scalars(select(Video).where(Video.platform == "instagram")).all()
    filled = already = missing = 0
    for v in rows:
        fp = fps.get(v.external_id)
        if v.duration_s is not None:
            already += 1
            continue
        if not fp or not fp.get("duration_s"):
            missing += 1
            continue
        v.duration_s = float(fp["duration_s"])
        filled += 1
    s.commit()
    print(f"  instagram durations: {filled} backfilled, {already} already "
          f"set, {missing} still unknown (no media fingerprint)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--covers-only", action="store_true")
    ap.add_argument("--durations-only", action="store_true")
    ap.add_argument("--account", default=None)
    args = ap.parse_args()

    eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    S = sessionmaker(eng, expire_on_commit=False, future=True)
    with S() as s:
        if not args.covers_only:
            do_ig_durations(s)
        if args.durations_only:
            return 0

        store = TokenStore(s)
        q = select(OAuthToken).where(OAuthToken.platform == "tiktok")
        if args.account:
            q = q.where(OAuthToken.account_ref == args.account)
        row = s.scalar(q)
        if row is None:
            print("  no TikTok credentials stored; skipping covers")
            return 0
        auth = TikTokAuth(os.environ["TIKTOK_CLIENT_KEY"],
                          os.environ["TIKTOK_CLIENT_SECRET"])
        adapter = TikTokOfficialAdapter(store, auth)
        do_covers(s, adapter, row.account_ref)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
