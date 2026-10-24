"""Preserve Instagram media before its URLs expire. Runs ON Ubuntu.

Instagram's `media_url` is a signed CDN link with a short life, so the window
to capture a post's media is narrow and does not reopen on demand. This
fetches **fresh** URLs every run — a URL saved during an earlier pass is
assumed dead, never retried.

Safeguards, each because the alternative fails badly at 289 files:

  * **bounded concurrency** — a handful of parallel fetches, not 289
  * **retries with backoff** on transient failures only
  * **deduplication** by content hash, so identical media stored once
  * **validation** — every file is ffprobed; a truncated or HTML error page
    is deleted rather than recorded as media
  * **storage cap** — refuses to exceed a configured budget
  * **resumable** — already-present, already-valid files are skipped, so an
    interrupted run costs only its remainder

    python download_ig_media.py --limit 10      # cautious first pass
    python download_ig_media.py                 # everything outstanding
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, "/home/rastlyn/virallab")

import httpx  # noqa: E402
from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from server.collectors.instagram_official import (  # noqa: E402
    InstagramOfficialAdapter, RateLimited,
)
from server.collectors.tokens import TokenStore  # noqa: E402
from server.db.models import OAuthToken, Video  # noqa: E402

MEDIA_ROOT = Path(os.environ.get("VIRALLAB_MEDIA_ROOT",
                                 "/home/rastlyn/virallab/media"))
CONCURRENCY = 4
MAX_RETRIES = 3
STORAGE_CAP_GB = float(os.environ.get("VIRALLAB_MEDIA_CAP_GB", "25"))
MIN_BYTES = 10_000            # anything smaller is an error page, not video


def ffprobe_ok(path: Path) -> bool:
    """A downloaded file is only media if ffprobe can decode its header."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name,duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, timeout=60, text=True,
            encoding="utf-8", errors="replace")
        return out.returncode == 0 and bool(out.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        return False


def fetch_one(ext_id: str, url: str, dest: Path) -> tuple[str, str, int, str]:
    """Download with retries. Returns (ext_id, status, bytes, sha256)."""
    tmp = dest.with_suffix(dest.suffix + ".part")
    last = ""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            h = hashlib.sha256()
            size = 0
            with httpx.stream("GET", url, timeout=120,
                              follow_redirects=True) as r:
                if r.status_code == 404:
                    return ext_id, "url_expired", 0, ""   # no point retrying
                r.raise_for_status()
                with tmp.open("wb") as f:
                    for chunk in r.iter_bytes(1 << 16):
                        f.write(chunk)
                        h.update(chunk)
                        size += len(chunk)
            if size < MIN_BYTES:
                tmp.unlink(missing_ok=True)
                return ext_id, "too_small", size, ""
            if not ffprobe_ok(tmp):
                tmp.unlink(missing_ok=True)
                return ext_id, "not_video", size, ""
            tmp.replace(dest)
            return ext_id, "ok", size, h.hexdigest()
        except httpx.HTTPError as exc:
            last = type(exc).__name__
            tmp.unlink(missing_ok=True)
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
    return ext_id, f"failed:{last}", 0, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
    eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    S = sessionmaker(eng, expire_on_commit=False, future=True)

    with S() as s:
        row = s.scalar(select(OAuthToken).where(
            OAuthToken.platform == "instagram"))
        if row is None:
            print("no instagram credential")
            return 1
        acct = row.account_ref
        adapter = InstagramOfficialAdapter(TokenStore(s))

        # Resumable: anything already stored AND still on disk is skipped.
        have = set()
        for v in s.scalars(select(Video).where(
                Video.platform == "instagram",
                Video.media_status == "available")).all():
            if v.media_path and Path(v.media_path).is_file():
                have.add(v.external_id)
            else:
                v.media_status = "absent"      # recorded but gone
        s.commit()
        print(f"  already preserved: {len(have)}")

        used = sum(f.stat().st_size for f in MEDIA_ROOT.glob("*.mp4"))
        print(f"  media dir in use  : {used/1e9:.2f} GB "
              f"(cap {STORAGE_CAP_GB} GB)")

        # FRESH urls only: a saved one is assumed dead.
        print("  fetching fresh media URLs…")
        targets = []
        try:
            for v in adapter.fetch_account_videos(acct):
                if v.external_id in have or not v.media_url:
                    continue
                targets.append((v.external_id, v.media_url))
        except RateLimited as exc:
            print(f"  rate limited while listing: {exc}")
        if args.limit:
            targets = targets[:args.limit]
        print(f"  to download: {len(targets)}")
        if not targets:
            return 0

        by_hash: dict[str, str] = {}
        results = {"ok": 0, "dup": 0, "url_expired": 0, "too_small": 0,
                   "not_video": 0, "failed": 0}
        written = 0

        with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
            futures = {
                pool.submit(fetch_one, ext, url,
                            MEDIA_ROOT / f"ig_{ext}.mp4"): ext
                for ext, url in targets}
            for fut in as_completed(futures):
                ext, status, size, digest = fut.result()
                if status == "ok":
                    if used + written + size > STORAGE_CAP_GB * 1e9:
                        print("  STORAGE CAP reached; stopping")
                        (MEDIA_ROOT / f"ig_{ext}.mp4").unlink(missing_ok=True)
                        break
                    if digest in by_hash:
                        # Identical media already stored under another id.
                        (MEDIA_ROOT / f"ig_{ext}.mp4").unlink(missing_ok=True)
                        results["dup"] += 1
                        path = MEDIA_ROOT / f"ig_{by_hash[digest]}.mp4"
                    else:
                        by_hash[digest] = ext
                        written += size
                        results["ok"] += 1
                        path = MEDIA_ROOT / f"ig_{ext}.mp4"
                    v = s.scalar(select(Video).where(
                        Video.platform == "instagram",
                        Video.external_id == ext))
                    if v is not None:
                        v.media_path = str(path)
                        v.media_status = "available"
                else:
                    key = status.split(":")[0]
                    results[key] = results.get(key, 0) + 1
        s.commit()

        print("\n  RESULTS")
        for k, n in results.items():
            if n:
                print(f"    {k:<12} {n}")
        print(f"    bytes written {written/1e9:.3f} GB")
        total = s.execute(text(
            "select count(*) from videos where platform='instagram' "
            "and media_status='available'")).scalar()
        print(f"    media_status='available': {total} / 289")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
