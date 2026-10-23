"""Phase 0B — encoding/storage benchmark across a range of real short-form video.

Replaces the single-video storage figure from Phase 0A, which was not a
representative measurement. Downloads a duration-stratified subset of videos
whose URLs were already collected in 0A, probes each with ffprobe, and
reports size-per-second against duration, resolution and bitrate.

This is a technical encoding benchmark on a handful of files, not corpus
collection. Downloaded media is gitignored.

    python bench_storage.py --urls ../data_access/bench_urls.local.json --n 14
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

FFPROBE = os.environ.get("FFPROBE", "ffprobe")
UA = {"User-Agent": "Mozilla/5.0"}


def stratified(pool: list, n: int) -> list:
    """Pick n items spread across the duration range, not the n shortest."""
    pool = sorted((p for p in pool if p[0]), key=lambda p: p[0])
    if len(pool) <= n:
        return pool
    step = (len(pool) - 1) / (n - 1)
    return [pool[round(i * step)] for i in range(n)]


def probe(path: Path) -> dict | None:
    cmd = [FFPROBE, "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=60, text=True)
        if out.returncode != 0:
            return None
        meta = json.loads(out.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
    v = next((s for s in meta.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in meta.get("streams", []) if s.get("codec_type") == "audio"), {})
    fmt = meta.get("format", {})
    fps = 0.0
    if v.get("avg_frame_rate", "0/0") not in ("0/0", None):
        num, _, den = v["avg_frame_rate"].partition("/")
        fps = float(num) / float(den) if float(den or 0) else 0.0
    return {
        "bytes": int(fmt.get("size") or path.stat().st_size),
        "duration_s": float(fmt.get("duration") or 0) or None,
        "bitrate_kbps": (int(fmt["bit_rate"]) / 1000) if fmt.get("bit_rate") else None,
        "width": v.get("width"), "height": v.get("height"),
        "vcodec": v.get("codec_name"), "acodec": a.get("codec_name"),
        "fps": round(fps, 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls", type=Path, required=True)
    ap.add_argument("--n", type=int, default=14)
    ap.add_argument("--outdir", type=Path, default=Path("media"))
    args = ap.parse_args()

    groups = json.loads(args.urls.read_text(encoding="utf-8"))
    pool = [(d, f"{src}_{i}", u)
            for src, items in groups.items()
            for i, (d, _vid, u) in enumerate(items) if u]
    picks = stratified(pool, args.n)
    args.outdir.mkdir(exist_ok=True)
    print("pool {} -> benchmarking {} files, durations {:.1f}s..{:.1f}s\n".format(
        len(pool), len(picks), picks[0][0], picks[-1][0]))

    rows, failed = [], 0
    for dur, name, url in picks:
        dest = args.outdir / f"{name}.mp4"
        t0 = time.monotonic()
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=90) as r:
                dest.write_bytes(r.read())
        except Exception as exc:
            print("  download FAILED {}: {}".format(name, type(exc).__name__))
            failed += 1
            continue
        dl = time.monotonic() - t0
        info = probe(dest)
        if not info:
            print("  ffprobe FAILED {}".format(name))
            failed += 1
            continue
        info.update(name=name, reported_duration_s=dur, download_s=round(dl, 2))
        info["mb"] = info["bytes"] / 1e6
        info["mb_per_s"] = info["mb"] / (info["duration_s"] or dur)
        rows.append(info)
        print("  {:<14} {:>6.1f}s {:>5}x{:<5} {:>7.2f}MB {:>7.3f}MB/s "
              "{:>6.0f}kbps {:>5.1f}fps {} dl={:.1f}s".format(
                  info["name"], info["duration_s"] or dur, info["width"],
                  info["height"], info["mb"], info["mb_per_s"],
                  info["bitrate_kbps"] or 0, info["fps"], info["vcodec"], dl))

    if not rows:
        print("\nno files measured")
        return 1

    per_s = [r["mb_per_s"] for r in rows]
    mbs = [r["mb"] for r in rows]
    durs = [r["duration_s"] or r["reported_duration_s"] for r in rows]
    res = sorted({(r["width"], r["height"]) for r in rows})
    br = [r["bitrate_kbps"] for r in rows if r["bitrate_kbps"]]

    print("\nMEASURED over n={} files ({} failed)".format(len(rows), failed))
    print("  duration range   : {:.1f}s .. {:.1f}s (median {:.1f}s)".format(
        min(durs), max(durs), statistics.median(durs)))
    print("  resolutions      : {}".format(
        ", ".join(f"{w}x{h}" for w, h in res)))
    print("  bitrate          : {:.0f} .. {:.0f} kbps (median {:.0f})".format(
        min(br), max(br), statistics.median(br)))
    print("  file size        : {:.2f} .. {:.2f} MB (median {:.2f})".format(
        min(mbs), max(mbs), statistics.median(mbs)))
    print("  MB per second    : {:.3f} .. {:.3f} (median {:.3f}, mean {:.3f})".format(
        min(per_s), max(per_s), statistics.median(per_s),
        statistics.fmean(per_s)))
    print("  spread           : max/min = {:.1f}x  <- why n=1 was not a measurement"
          .format(max(per_s) / min(per_s)))

    med_mb = statistics.median(mbs)
    p90_mb = sorted(mbs)[int(len(mbs) * 0.9) - 1] if len(mbs) >= 10 else max(mbs)
    print("\n  STORAGE PROJECTION (per-file median {:.2f}MB, p90 {:.2f}MB)".format(
        med_mb, p90_mb))
    print("  {:>10}  {:>12}  {:>12}".format("corpus", "median-based", "p90-based"))
    for n in (1000, 10000, 100000):
        print("  {:>10,}  {:>10.0f} GB  {:>10.0f} GB".format(
            n, med_mb * n / 1000, p90_mb * n / 1000))
    print("\n  Note: median-based is the planning figure; p90 is the headroom"
          "\n  figure. Both assume this niche's encoding profile.")

    out = Path("storage_bench_results.json")
    out.write_text(json.dumps({
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_files": len(rows), "n_failed": failed, "rows": rows,
        "median_mb": med_mb, "p90_mb": p90_mb,
        "median_mb_per_s": statistics.median(per_s),
    }, indent=2), encoding="utf-8")
    print("\n  raw results -> {}".format(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
