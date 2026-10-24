"""Perceptual fingerprints for matching local masters to published posts.

A published post is a re-encode of its master: different bitrate, often a
different resolution, sometimes cropped or trimmed. So byte hashes are
useless and the fingerprint has to survive all of that.

What is extracted per video:

  * a **sequence** of 64-bit dHashes from evenly sampled frames, and
  * the duration.

The sequence is the important part. A single hash can only answer "are these
the same video". A sequence can answer "does this post contain a stretch of
that master", which is what identifies **edited variants and reposts**, and
what lets **one master map to several posts**.

dHash (difference hash) is used rather than aHash because it is robust to
brightness and contrast shifts, which re-encoding introduces freely.

Pure standard library plus Pillow — no extra dependency.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image

FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE", "ffprobe")

#: Frames sampled per video. Enough to characterise a 15-60 s clip without
#: making the scan expensive; a partial-overlap match needs several.
FRAMES = 12
HASH_W, HASH_H = 9, 8          # dHash compares 8 adjacent pairs per row


def duration_of(path: Path) -> float | None:
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, timeout=60, text=True,
            encoding="utf-8", errors="replace")
        return float(out.stdout.strip()) or None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def dhash_frame(img: Image.Image) -> int:
    """64-bit difference hash: each bit is 'this pixel brighter than next'."""
    small = img.convert("L").resize((HASH_W, HASH_H), Image.LANCZOS)
    px = list(small.getdata())  # noqa: Pillow 14 renames this
    bits = 0
    for row in range(HASH_H):
        base = row * HASH_W
        for col in range(HASH_W - 1):
            bits = (bits << 1) | int(px[base + col] > px[base + col + 1])
    return bits


# The spectral fingerprint lives in shared/ because it is core to
# matching and runs on BOTH machines - the server fingerprints Instagram
# media in place rather than shipping 736 MB to Windows.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
try:
    from shared import audiofp
except ImportError:                                   # pragma: no cover
    audiofp = None


def fingerprint(path: Path, workdir: Path, frames: int = FRAMES
                ) -> dict | None:
    """Sampled dHash sequence for one video."""
    dur = duration_of(path)
    if not dur or dur <= 0:
        return None
    workdir.mkdir(parents=True, exist_ok=True)
    for old in workdir.glob("f*.png"):
        old.unlink()
    # Sample across the whole clip but avoid the very first and last frames,
    # which are often black or a platform-added end card.
    fps = frames / max(dur * 0.92, 0.1)
    try:
        subprocess.run(
            [FFMPEG, "-v", "error", "-y", "-ss", f"{dur*0.04:.3f}",
             "-i", str(path), "-vf", f"fps={fps:.6f},scale=64:64",
             "-frames:v", str(frames), str(workdir / "f%03d.png")],
            capture_output=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired):
        return None

    hashes = []
    for f in sorted(workdir.glob("f*.png")):
        try:
            with Image.open(f) as im:
                hashes.append(dhash_frame(im))
        except OSError:
            continue
        finally:
            f.unlink(missing_ok=True)
    if len(hashes) < 3:
        return None
    return {"duration_s": dur, "frames": len(hashes),
            "hashes": [f"{h:016x}" for h in hashes],
            # Both audio descriptors are stored. `audiofp` is the one that
            # decides matches; `audio` is kept so fingerprints written before
            # the spectral method existed remain comparable rather than
            # silently becoming unverifiable.
            "audio": audio_envelope(path),
            "audiofp": audiofp.fingerprint(path) if audiofp else None}


def audio_envelope(path: Path, bins: int = 32) -> list[int] | None:
    """Coarse loudness envelope, as `bins` buckets of 0-255.

    This exists because frame hashing alone produced confident FALSE
    matches: the owner's "musical portraits" series are visually identical
    (same framing, same piano, ~9 s each) and differ only in the music. A
    visual fingerprint cannot tell them apart, so the audio has to.

    Deliberately coarse — it must survive re-encoding, not identify a track.
    """
    try:
        out = subprocess.run(
            [FFMPEG, "-v", "error", "-i", str(path), "-vn",
             "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
            capture_output=True, timeout=180)
        raw = out.stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    if not raw or len(raw) < 2000:
        return None

    import array
    pcm = array.array("h")
    pcm.frombytes(raw[: len(raw) - (len(raw) % 2)])
    if not pcm:
        return None
    step = max(1, len(pcm) // bins)
    env = []
    for i in range(bins):
        chunk = pcm[i * step:(i + 1) * step]
        if not chunk:
            env.append(0)
            continue
        mean_abs = sum(abs(x) for x in chunk) / len(chunk)
        env.append(min(255, int(mean_abs / 128)))
    peak = max(env) or 1
    return [int(v * 255 / peak) for v in env]       # normalise loudness


def envelope_similarity(a: list[int] | None, b: list[int] | None) -> float:
    """1.0 = identical envelope, 0.0 = unrelated. None-safe."""
    if not a or not b or len(a) != len(b):
        return 0.0
    diff = sum(abs(x - y) for x, y in zip(a, b)) / len(a)
    return max(0.0, 1.0 - diff / 128.0)


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def best_overlap(a: list[int], b: list[int], *, max_dist: int = 12
                 ) -> tuple[float, int]:
    """Best alignment score between two hash sequences.

    Slides the shorter sequence across the longer one, so a post that uses
    only part of a master still scores on the overlapping stretch. Returns
    (fraction of matched frames, best offset).
    """
    if not a or not b:
        return 0.0, 0
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    best, best_off = 0.0, 0
    for off in range(len(long_) - len(short) + 1):
        hits = sum(1 for i, h in enumerate(short)
                   if hamming(h, long_[off + i]) <= max_dist)
        score = hits / len(short)
        if score > best:
            best, best_off = score, off
    return best, best_off


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--inventory", type=Path,
                    default=Path("probes/local_library/inventory.json"))
    ap.add_argument("--out", type=Path,
                    default=Path("probes/local_library/fingerprints.json"))
    ap.add_argument("--max-duration", type=float, default=180.0,
                    help="skip long-form; it cannot be a short-form post")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    rows = json.loads(args.inventory.read_text(encoding="utf-8"))
    rows = [r for r in rows if (r.get("duration_s") or 1e9) <= args.max_duration]
    if args.limit:
        rows = rows[:args.limit]
    print(f"  fingerprinting {len(rows)} short-form candidates")

    work = Path("probes/local_library/_frames")
    out: dict[str, dict] = {}
    existing = {}
    if args.out.exists():
        existing = json.loads(args.out.read_text(encoding="utf-8"))
        print(f"  resuming: {len(existing)} already done")

    for i, r in enumerate(rows, 1):
        key = r["quick_id"]
        if key in existing:
            out[key] = existing[key]
            continue
        fp = fingerprint(Path(r["path"]), work)
        if fp:
            fp["path"] = r["path"]
            fp["name"] = r["name"]
            out[key] = fp
        if i % 25 == 0:
            print(f"    … {i}/{len(rows)}")
            args.out.write_text(json.dumps(out), encoding="utf-8")

    args.out.write_text(json.dumps(out), encoding="utf-8")
    print(f"  fingerprinted {len(out)} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
