"""Phase 0B — corrected Whisper measurement.

The first benchmark run reported an RTF median of 328x with EMPTY transcripts
on every video. That number was not a throughput measurement: the VAD filter
classified the audio as non-speech and skipped nearly all of it, so almost no
compute was done. Classical-piano content is largely music-only.

Two separate things therefore need measuring, and conflating them is what
produced the bogus figure:

  1. THROUGHPUT  - run with vad_filter=False so the encoder/decoder actually
                   processes the full audio. This is the worst-case cost to
                   use for projections.
  2. USEFULNESS  - does the transcript contain anything? For music-only
                   content the answer is no, which is a domain finding, not
                   a defect.

Also re-times librosa, because the 19.2s first call in the main benchmark was
numba JIT compilation, not per-video cost.
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    for _sub in ("nvidia/cublas/bin", "nvidia/cudnn/bin"):
        _d = Path(sys.prefix) / "Lib" / "site-packages" / _sub
        if _d.is_dir():
            os.add_dll_directory(str(_d))

FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE", "ffprobe")


def duration(p: Path) -> float:
    out = subprocess.run([FFPROBE, "-v", "error", "-show_entries",
                          "format=duration", "-of", "csv=p=0", str(p)],
                         capture_output=True, text=True, timeout=60)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def main() -> int:
    media = Path("media")
    vids = sorted(media.glob("*.mp4"))
    if not vids:
        print("no media")
        return 1

    import librosa
    import numpy as np
    from faster_whisper import WhisperModel

    work = Path("_work_wfix")
    work.mkdir(exist_ok=True)
    print("decoding audio for {} videos...".format(len(vids)))
    audio = {}
    for v in vids:
        wav = work / (v.stem + ".wav")
        if not wav.exists():
            subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(v), "-vn",
                            "-ac", "1", "-ar", "16000", str(wav)],
                           capture_output=True, timeout=300)
        audio[v] = librosa.load(str(wav), sr=16000, mono=True)[0]

    out: dict = {"whisper": {}, "librosa": {}}

    for size in ("small", "medium"):
        print("\n=== WHISPER '{}' int8, vad_filter=False (true compute cost) ===".format(size))
        t0 = time.monotonic()
        model = WhisperModel(size, device="cuda", compute_type="int8")
        load_s = time.monotonic() - t0
        print("  cold load: {:.2f}s".format(load_s))
        rows = []
        for v in vids:
            dur = duration(v)
            t1 = time.monotonic()
            segs, info = model.transcribe(audio[v], beam_size=1,
                                          vad_filter=False, language="en")
            text = " ".join(s.text for s in segs).strip()
            el = time.monotonic() - t1
            rtf = dur / el if el else 0
            rows.append({"video": v.name, "duration_s": dur, "seconds": el,
                         "rtf": rtf, "chars": len(text),
                         "text_head": text[:120]})
            print("  {:<16} {:>6.1f}s -> {:>6.2f}s  {:>5.1f}x RT  {:>4} chars  {}"
                  .format(v.name[:16], dur, el, rtf, len(text),
                          (text[:46] + "...") if text else "(no speech found)"))
        rtfs = [r["rtf"] for r in rows]
        spoken = [r for r in rows if r["chars"] > 20]
        out["whisper"][size] = {
            "cold_load_s": load_s, "rows": rows,
            "rtf_median": statistics.median(rtfs),
            "rtf_min": min(rtfs), "rtf_max": max(rtfs),
            "videos_with_speech": len(spoken), "videos_total": len(rows),
        }
        print("  --> TRUE RTF median {:.1f}x  (min {:.1f}x, max {:.1f}x)".format(
            statistics.median(rtfs), min(rtfs), max(rtfs)))
        print("  --> transcript non-trivial on {}/{} videos".format(
            len(spoken), len(rows)))
        del model
        time.sleep(2)

    print("\n=== LIBROSA: JIT warmup vs steady state ===")
    y = audio[vids[0]]
    t0 = time.monotonic()
    librosa.beat.beat_track(y=y, sr=16000)
    librosa.feature.spectral_centroid(y=y, sr=16000)
    first = time.monotonic() - t0
    warm = []
    for v in vids[1:7]:
        t1 = time.monotonic()
        librosa.beat.beat_track(y=audio[v], sr=16000)
        librosa.feature.rms(y=audio[v])
        librosa.feature.spectral_centroid(y=audio[v], sr=16000)
        warm.append((duration(v), time.monotonic() - t1))
    per_s = [t / d for d, t in warm if d]
    print("  first call (incl. numba JIT) : {:.2f}s".format(first))
    print("  warm calls                   : {}".format(
        ", ".join("{:.2f}s".format(t) for _, t in warm)))
    print("  --> steady state {:.3f}s per audio-second ({:.0f}x realtime)".format(
        statistics.median(per_s), 1 / statistics.median(per_s)))
    out["librosa"] = {"first_call_s": first,
                      "warm_s_per_audio_s": statistics.median(per_s),
                      "warm_rows": warm}

    Path("whisper_fix_results.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8")
    print("\n  raw -> whisper_fix_results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
