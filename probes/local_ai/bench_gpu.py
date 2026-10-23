"""Phase 0B — local AI throughput / VRAM / quality benchmark on the real GPU.

Measures each cost component separately, per DEVELOPMENT_PLAN.md section 7
Phase 0B. Reporting inference time alone understates real cost, and model
cold-load time is the component most likely to dominate for short clips.

Stages timed independently:
  decode      ffmpeg frame extraction + audio extraction
  whisper     cold model load, then transcription (per model size)
  vlm         cold load (first request), then warm per-frame inference
  shots       OpenCV/scene-change detection
  audio       librosa feature extraction

Peak VRAM is sampled by polling nvidia-smi in a background thread for the
duration of each stage, so the figure includes whatever else is resident on
the card (desktop compositor included) - which is the number that actually
matters on an 8GB consumer GPU.

    python bench_gpu.py --media media --whisper-sizes small medium
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

# CTranslate2 needs cuBLAS/cuDNN at runtime. The pip nvidia-* wheels ship the
# DLLs but do not put them on the loader path, so register them explicitly
# before faster_whisper is imported.
if sys.platform == "win32":
    for _sub in ("nvidia/cublas/bin", "nvidia/cudnn/bin"):
        _d = Path(sys.prefix) / "Lib" / "site-packages" / _sub
        if _d.is_dir():
            os.add_dll_directory(str(_d))

FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
VLM_MODEL = os.environ.get("VLM_MODEL", "qwen2.5vl:7b")

VLM_PROMPT = (
    "You are analysing one frame from a short-form vertical video. "
    "Answer in compact JSON with exactly these keys: "
    "shot_type (closeup/medium/wide), camera_angle (eye/high/low/overhead), "
    "subject (what is in frame, <=6 words), "
    "on_screen_text (verbatim text visible, or null), "
    "lighting (natural/artificial/mixed/dark), "
    "mood (<=3 words). JSON only, no prose."
)


class VramSampler(threading.Thread):
    """Polls nvidia-smi so peak VRAM covers the whole stage, not one instant."""

    def __init__(self, interval: float = 0.25):
        super().__init__(daemon=True)
        self.interval, self.samples = interval, []
        self._halt = threading.Event()

    def run(self):
        while not self._halt.is_set():
            try:
                out = subprocess.run(
                    ["nvidia-smi", "--query-gpu=memory.used",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=10)
                if out.returncode == 0:
                    self.samples.append(int(out.stdout.strip().splitlines()[0]))
            except (OSError, ValueError, subprocess.TimeoutExpired, IndexError):
                pass
            self._halt.wait(self.interval)

    def stop(self) -> dict:
        self._halt.set()
        self.join(timeout=5)
        if not self.samples:
            return {"peak_mib": None, "mean_mib": None, "n": 0}
        return {"peak_mib": max(self.samples),
                "mean_mib": round(statistics.fmean(self.samples)),
                "n": len(self.samples)}


class timed:
    """Context manager: wall time + peak VRAM for one stage."""

    def __init__(self, label: str, quiet: bool = False):
        self.label, self.quiet = label, quiet

    def __enter__(self):
        self.sampler = VramSampler()
        self.sampler.start()
        self.t0 = time.monotonic()
        return self

    def __exit__(self, *exc):
        self.seconds = time.monotonic() - self.t0
        self.vram = self.sampler.stop()
        if not self.quiet:
            print("    {:<34} {:>8.2f}s  peak {:>6} MiB".format(
                self.label, self.seconds, self.vram["peak_mib"]))
        return False


def gpu_info() -> dict:
    out = subprocess.run(
        ["nvidia-smi",
         "--query-gpu=name,memory.total,memory.used,driver_version",
         "--format=csv,noheader"], capture_output=True, text=True)
    name, total, used, drv = [x.strip() for x in out.stdout.strip().split(",")]
    return {"gpu": name, "vram_total": total, "vram_idle_baseline": used,
            "driver": drv}


def extract(video: Path, workdir: Path, n_frames: int) -> tuple[list[Path], Path, float]:
    """Uniformly sample n_frames and extract 16kHz mono audio. Returns timing."""
    workdir.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    pat = workdir / "f%03d.jpg"
    subprocess.run(
        [FFMPEG, "-v", "error", "-y", "-i", str(video),
         "-vf", f"fps={n_frames}/{max(1, probe_duration(video)):.6f},scale=448:-2",
         "-frames:v", str(n_frames), "-q:v", "4", str(pat)],
        capture_output=True, timeout=300)
    audio = workdir / "audio.wav"
    subprocess.run(
        [FFMPEG, "-v", "error", "-y", "-i", str(video),
         "-vn", "-ac", "1", "-ar", "16000", str(audio)],
        capture_output=True, timeout=300)
    frames = sorted(workdir.glob("f*.jpg"))
    return frames, audio, time.monotonic() - t0


_dur_cache: dict[str, float] = {}


def probe_duration(video: Path) -> float:
    key = str(video)
    if key not in _dur_cache:
        out = subprocess.run(
            [os.environ.get("FFPROBE", "ffprobe"), "-v", "error",
             "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
            capture_output=True, text=True, timeout=60)
        try:
            _dur_cache[key] = float(out.stdout.strip())
        except ValueError:
            _dur_cache[key] = 0.0
    return _dur_cache[key]


def vlm_call(image: Path, timeout: int = 300) -> tuple[str, float]:
    payload = {
        "model": VLM_MODEL, "prompt": VLM_PROMPT, "stream": False,
        "images": [base64.b64encode(image.read_bytes()).decode()],
        "options": {"temperature": 0, "num_predict": 160},
    }
    req = urllib.request.Request(
        f"{OLLAMA}/api/generate", method="POST",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.loads(r.read())
    return body.get("response", ""), time.monotonic() - t0


def unload_vlm():
    """Ask ollama to evict the model so the next call measures a cold load."""
    try:
        req = urllib.request.Request(
            f"{OLLAMA}/api/generate", method="POST",
            data=json.dumps({"model": VLM_MODEL, "keep_alive": 0}).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=60).read()
        time.sleep(4)
    except Exception:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--media", type=Path, default=Path("media"))
    ap.add_argument("--whisper-sizes", nargs="+", default=["small"])
    ap.add_argument("--frames", type=int, default=4,
                    help="frames sampled per video for Tier 2")
    ap.add_argument("--tier3-frames", type=int, default=12)
    ap.add_argument("--limit", type=int, default=6)
    ap.add_argument("--out", type=Path, default=Path("gpu_bench_results.json"))
    args = ap.parse_args()

    vids = sorted(p for p in args.media.glob("*.mp4"))[:args.limit]
    if not vids:
        print("no .mp4 files in {}".format(args.media))
        return 1

    info = gpu_info()
    print("\nPhase 0B GPU benchmark")
    for k, v in info.items():
        print("  {:<20} {}".format(k, v))
    print("  {:<20} {}".format("videos", len(vids)))
    print("  {:<20} {} (Tier2) / {} (Tier3)".format(
        "frames per video", args.frames, args.tier3_frames))

    results = {"gpu": info, "videos": [], "whisper": {}, "vlm": {},
               "stages": {}, "quality_samples": []}
    work = Path("_work")

    # ---------- decode -------------------------------------------------------
    print("\n[1] DECODE (ffmpeg frame + audio extraction)")
    decode_times, frame_sets = [], {}
    for v in vids:
        with timed("{}".format(v.name[:30]), quiet=True) as t:
            frames, audio, dt = extract(v, work / v.stem, args.frames)
        frame_sets[v] = (frames, audio)
        dur = probe_duration(v)
        decode_times.append((dur, dt))
        print("    {:<22} {:>6.1f}s video -> {:>6.2f}s decode "
              "({:>5.1f}x realtime, {} frames)".format(
                  v.name[:22], dur, dt, dur / dt if dt else 0, len(frames)))
    results["stages"]["decode"] = [
        {"duration_s": d, "decode_s": t} for d, t in decode_times]

    # ---------- whisper ------------------------------------------------------
    from faster_whisper import WhisperModel  # imported late: load cost is measured
    import librosa as _lb

    # Pre-decode audio outside the timed region so transcription timing is
    # inference only (decode is already measured separately in stage 1).
    pcm = {}
    for v in vids:
        _, audio = frame_sets[v]
        pcm[v], _sr = _lb.load(str(audio), sr=16000, mono=True)

    for size in args.whisper_sizes:
        print("\n[2] WHISPER '{}' (int8)".format(size))
        with timed("cold model load", quiet=True) as tl:
            model = WhisperModel(size, device="cuda", compute_type="int8")
        print("    {:<34} {:>8.2f}s  peak {:>6} MiB".format(
            "cold model load", tl.seconds, tl.vram["peak_mib"]))
        per_video = []
        for v in vids:
            _, audio = frame_sets[v]
            dur = probe_duration(v)
            with timed("transcribe", quiet=True) as tt:
                segs, inf = model.transcribe(pcm[v], beam_size=1,
                                             vad_filter=True)
                text = " ".join(s.text for s in segs).strip()
            rtf = dur / tt.seconds if tt.seconds else 0
            per_video.append({"video": v.name, "duration_s": dur,
                              "transcribe_s": tt.seconds, "rtf": rtf,
                              "peak_mib": tt.vram["peak_mib"],
                              "lang": inf.language,
                              "chars": len(text)})
            print("    {:<22} {:>6.1f}s -> {:>6.2f}s  {:>5.1f}x RT  "
                  "lang={} {:>4} chars  peak {} MiB".format(
                      v.name[:22], dur, tt.seconds, rtf, inf.language,
                      len(text), tt.vram["peak_mib"]))
            results["quality_samples"].append(
                {"stage": "whisper-" + size, "video": v.name,
                 "text": text[:400]})
        rtfs = [p["rtf"] for p in per_video]
        results["whisper"][size] = {
            "cold_load_s": tl.seconds, "cold_load_peak_mib": tl.vram["peak_mib"],
            "per_video": per_video,
            "rtf_median": statistics.median(rtfs),
            "rtf_min": min(rtfs), "rtf_max": max(rtfs)}
        print("    --> RTF median {:.1f}x  (min {:.1f}x, max {:.1f}x)".format(
            statistics.median(rtfs), min(rtfs), max(rtfs)))
        del model
        time.sleep(2)

    # ---------- VLM ----------------------------------------------------------
    print("\n[3] VLM '{}' (ollama)".format(VLM_MODEL))
    unload_vlm()
    first = frame_sets[vids[0]][0]
    if not first:
        print("    no frames extracted; skipping VLM")
        return 1
    try:
        with timed("cold load + first frame", quiet=True) as tc:
            text0, _ = vlm_call(first[0])
        print("    {:<34} {:>8.2f}s  peak {:>6} MiB".format(
            "cold load + 1st frame", tc.seconds, tc.vram["peak_mib"]))
    except (urllib.error.URLError, TimeoutError) as exc:
        print("    VLM unreachable at {}: {}".format(OLLAMA, exc))
        return 1

    warm = []
    for v in vids:
        frames, _ = frame_sets[v]
        with timed("vlm", quiet=True) as tv:
            outs = [vlm_call(f)[0] for f in frames]
        per_frame = tv.seconds / max(1, len(frames))
        warm.append({"video": v.name, "frames": len(frames),
                     "total_s": tv.seconds, "per_frame_s": per_frame,
                     "peak_mib": tv.vram["peak_mib"]})
        print("    {:<22} {:>2} frames -> {:>7.2f}s  {:>6.2f}s/frame  "
              "peak {} MiB".format(v.name[:22], len(frames), tv.seconds,
                                   per_frame, tv.vram["peak_mib"]))
        results["quality_samples"].append(
            {"stage": "vlm", "video": v.name, "text": outs[0][:500]})
    pf = [w["per_frame_s"] for w in warm]
    results["vlm"] = {
        "model": VLM_MODEL,
        "cold_load_plus_first_s": tc.seconds,
        "cold_load_peak_mib": tc.vram["peak_mib"],
        "warm": warm,
        "per_frame_median_s": statistics.median(pf),
        "cold_load_only_s": max(0.0, tc.seconds - statistics.median(pf)),
    }
    print("    --> warm {:.2f}s/frame median; cold-load overhead ~{:.1f}s".format(
        statistics.median(pf), results["vlm"]["cold_load_only_s"]))

    # ---------- classic CV + audio ------------------------------------------
    print("\n[4] CLASSIC CV + AUDIO (CPU)")
    import cv2
    import librosa
    import numpy as np
    cv_rows = []
    for v in vids:
        dur = probe_duration(v)
        with timed("shots", quiet=True) as ts:
            cap = cv2.VideoCapture(str(v))
            prev, cuts, n = None, 0, 0
            while True:
                ok, fr = cap.read()
                if not ok:
                    break
                n += 1
                if n % 3:
                    continue
                small = cv2.resize(fr, (160, 284))
                hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
                hist = cv2.calcHist([hsv], [0, 1], None, [32, 32],
                                    [0, 180, 0, 256])
                cv2.normalize(hist, hist)
                if prev is not None and cv2.compareHist(
                        prev, hist, cv2.HISTCMP_CORREL) < 0.7:
                    cuts += 1
                prev = hist
            cap.release()
        _, audio = frame_sets[v]
        with timed("audio", quiet=True) as ta:
            y, sr = librosa.load(str(audio), sr=16000, mono=True)
            tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
            rms = float(np.mean(librosa.feature.rms(y=y)))
            cent = float(np.mean(librosa.feature.spectral_centroid(y=y, sr=sr)))
        cv_rows.append({"video": v.name, "duration_s": dur,
                        "shots_s": ts.seconds, "audio_s": ta.seconds,
                        "cuts": cuts, "frames_read": n,
                        "tempo": float(np.ravel(tempo)[0]), "rms": rms,
                        "centroid": cent})
        print("    {:<22} shots {:>6.2f}s ({} cuts) | audio {:>5.2f}s "
              "(tempo {:.0f}bpm)".format(v.name[:22], ts.seconds, cuts,
                                         ta.seconds, float(np.ravel(tempo)[0])))
    results["stages"]["cv_audio"] = cv_rows

    args.out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("\n  raw results -> {}".format(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
