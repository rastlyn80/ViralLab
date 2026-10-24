"""Inventory the local video library and fingerprint it for matching.

**Strictly read-only.** Originals are never modified, moved, renamed or
deleted. Everything produced lands in a separate output directory.

Two stages, deliberately separated so a large transfer is never a surprise:

  1. ``--inventory``  walk the trees, ffprobe each file, report sizes and
     durations. Cheap, no decoding of frames.
  2. ``--fingerprint`` extract perceptual hashes for matching. Costs a few
     hundred ms per file, so it is opt-in after the inventory is reviewed.

Matching strategy (why these fingerprints):

  * **Duration** alone is a weak signal but a near-free filter.
  * **pHash of sampled frames** survives re-encoding, resolution changes and
    moderate cropping — which is what a published post is relative to its
    master.
  * A **sequence** of frame hashes, not one, so an edited variant that shares
    only part of its footage still matches on the overlapping stretch. That
    is what lets one source video link to several published posts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

FFPROBE = os.environ.get("FFPROBE", "ffprobe")
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".mpg", ".mpeg",
             ".wmv", ".flv", ".m2ts", ".mts", ".3gp"}
#: Directories that never hold source material.
SKIP_DIRS = {"$RECYCLE.BIN", "System Volume Information", ".git",
             "__pycache__", "node_modules", "Adobe Premiere Pro Auto-Save",
             "Adobe Premiere Pro Audio Previews",
             "Adobe Premiere Pro Video Previews", "Motion Graphics Templates"}

OUT = Path("probes/local_library")


def iter_videos(roots: list[Path]):
    for root in roots:
        if not root.exists():
            print(f"  WARNING: {root} does not exist", file=sys.stderr)
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                p = Path(dirpath) / name
                if p.suffix.lower() in VIDEO_EXT:
                    yield p


def probe(path: Path) -> dict | None:
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, timeout=90, text=True,
            # Media metadata carries bytes the Windows default codec cannot
            # decode; replace rather than abort the whole scan on one file.
            encoding="utf-8", errors="replace")
        if out.returncode != 0 or not out.stdout:
            return None
        meta = json.loads(out.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError,
            UnicodeDecodeError, TypeError):
        return None
    v = next((s for s in meta.get("streams", [])
              if s.get("codec_type") == "video"), None)
    if v is None:
        return None                      # audio-only file with a video ext
    fmt = meta.get("format", {})
    tags = {k.lower(): val for k, val in (fmt.get("tags") or {}).items()}
    return {
        "duration_s": float(fmt.get("duration") or 0) or None,
        "bytes": int(fmt.get("size") or path.stat().st_size),
        "bitrate_kbps": (int(fmt["bit_rate"]) // 1000) if fmt.get("bit_rate") else None,
        "width": v.get("width"), "height": v.get("height"),
        "vcodec": v.get("codec_name"),
        "creation_time": tags.get("creation_time"),
    }


def quick_id(path: Path, info: dict) -> str:
    """Stable identity for a file that does not require reading all of it."""
    h = hashlib.sha256()
    h.update(str(info["bytes"]).encode())
    h.update(f"{info['duration_s']:.3f}".encode())
    with path.open("rb") as f:
        h.update(f.read(1 << 20))         # first 1 MiB
        if info["bytes"] > (2 << 20):
            f.seek(-(1 << 20), os.SEEK_END)
            h.update(f.read(1 << 20))     # last 1 MiB
    return h.hexdigest()[:32]


def inventory(roots: list[Path], limit: int | None) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    rows, failed, skipped_audio = [], [], 0
    seen = 0
    for p in iter_videos(roots):
        seen += 1
        if limit and len(rows) >= limit:
            break
        info = probe(p)
        if info is None:
            failed.append(str(p))
            continue
        if not info["duration_s"]:
            skipped_audio += 1
            continue
        try:
            qid = quick_id(p, info)
        except OSError:
            failed.append(str(p))
            continue
        rows.append({
            "path": str(p), "name": p.name,
            "root": str(next(r for r in roots if str(p).startswith(str(r)))),
            "quick_id": qid, "mtime": datetime.fromtimestamp(
                p.stat().st_mtime, tz=timezone.utc).isoformat(), **info})
        if len(rows) % 50 == 0:
            print(f"    … {len(rows)} probed")

    out = OUT / "inventory.json"
    out.write_text(json.dumps(rows, indent=1), encoding="utf-8")

    total_bytes = sum(r["bytes"] for r in rows)
    durations = sorted(r["duration_s"] for r in rows)
    dupes = Counter(r["quick_id"] for r in rows)
    dupe_groups = {k: v for k, v in dupes.items() if v > 1}
    short = [r for r in rows if r["duration_s"] <= 180]
    short_bytes = sum(r["bytes"] for r in short)

    print()
    print("=== INVENTORY ===")
    print(f"  files seen with a video extension : {seen}")
    print(f"  probed successfully               : {len(rows)}")
    print(f"  unreadable / not video            : {len(failed)} / {skipped_audio}")
    print(f"  total size                        : {total_bytes/1e9:.1f} GB")
    if durations:
        print(f"  duration  min/median/max          : "
              f"{durations[0]:.0f}s / {durations[len(durations)//2]:.0f}s / "
              f"{durations[-1]:.0f}s")
    print(f"  exact duplicates (same quick_id)  : "
          f"{sum(v-1 for v in dupe_groups.values())} extra copies in "
          f"{len(dupe_groups)} group(s)")
    print()
    print("  SHORT-FORM CANDIDATES (<= 180 s, the plausible post sources)")
    print(f"    count : {len(short)}")
    print(f"    size  : {short_bytes/1e9:.2f} GB  "
          f"<- only these need transferring")
    print()
    by_root = Counter(r["root"] for r in rows)
    for root, n in by_root.items():
        gb = sum(r["bytes"] for r in rows if r["root"] == root) / 1e9
        print(f"    {root}: {n} files, {gb:.1f} GB")
    res = Counter(f"{r['width']}x{r['height']}" for r in rows)
    print("\n  top resolutions:", ", ".join(
        f"{k} ({v})" for k, v in res.most_common(5)))
    vert = sum(1 for r in rows
               if r["width"] and r["height"] and r["height"] > r["width"])
    print(f"  vertical (height > width)         : {vert} "
          f"<- short-form shape")
    print(f"\n  inventory -> {out}")
    if failed:
        (OUT / "unreadable.txt").write_text("\n".join(failed), encoding="utf-8")
        print(f"  unreadable list -> {OUT/'unreadable.txt'}")
    return {"rows": rows, "failed": failed}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--roots", nargs="+",
                    default=[r"D:\Videos", r"D:\Portraits\Exports"])
    ap.add_argument("--inventory", action="store_true")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    roots = [Path(r) for r in args.roots]
    print("ViralLab local library scan (READ-ONLY; nothing is modified)")
    for r in roots:
        print(f"  root: {r}  {'ok' if r.exists() else 'MISSING'}")
    print()
    if args.inventory:
        inventory(roots, args.limit)
        return 0
    print("  pass --inventory to walk the trees")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
