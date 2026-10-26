"""Deterministic measurement of a video's opening seconds.

Phase 6A. The point of this module is the line it draws: everything in here
is **signal processing on the actual file**, reproducible to the bit from the
same input, with no model and no interpretation. A later pass may add model
judgements (what the text says, whether a piano is visible, what mood the
opening projects); those are a different kind of claim and are stored with a
different `method`, never mixed into these numbers.

Why it runs with only ffmpeg, numpy and PIL: those are the three things the
authoritative server already has, the 258 preserved files already live there,
and `DEVELOPMENT_PLAN.md` forbids moving large amounts of local master media
around. Adding OpenCV or librosa to decode frames we can already decode
would be a dependency bought for convenience (CLAUDE.md 6).

**Every feature carries its own uncertainty and the interval it was measured
over.** A threshold-derived boolean is reported with the margin by which it
cleared the threshold, because "text is present" from an edge-density proxy
is a far weaker claim than "the audio track exists", and a consumer that
cannot tell them apart will eventually treat both as facts.

Measured on the real corpus, not assumed:

  * decode of the first 3 s at 10 fps and 16 kHz mono costs well under a
    second per file on the server's 4 cores, so no GPU is involved and no
    batching is needed;
  * `reels_skip_rate` is the retention figure a hook should move. Instagram's
    `ig_reels_avg_watch_time` **includes replays** - one real video in this
    corpus averages 3.38x its own duration - so it is not a completion
    fraction and is never used as one.
"""

from __future__ import annotations

import json
import logging
import math
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

logger = logging.getLogger("virallab.hookfeat")

#: Version of this extractor. Bumping it makes a reprocessing run store a
#: NEW row rather than overwrite the old one, so a method change never
#: silently rewrites history.
METHOD_VERSION = "hook-deterministic-v1"

#: The window this module is about.
HOOK_SECONDS = 3.0
#: Frame sampling rate inside the window. 10 fps resolves a cut to 100 ms,
#: which is finer than any editorial decision being measured, and keeps the
#: decode cheap.
FRAME_FPS = 10.0
#: Frames are analysed at this width; height follows the source aspect.
#: Small on purpose: every statistic here is a global or regional average,
#: and none of them need pixels.
ANALYSIS_WIDTH = 160
#: Audio is analysed mono at this rate. 16 kHz keeps the whole speech band
#: and halves the work against 32 kHz.
AUDIO_SR = 16000
#: Audio analysis frame and hop, in samples. 25 ms / 10 ms is the standard
#: speech framing and is fine for music too.
AUDIO_FRAME = 400
AUDIO_HOP = 160

#: A later window used only for novelty: how different the opening is from
#: the rest of the video. Sampled, not fully decoded.
COMPARISON_START_S = 5.0
COMPARISON_SECONDS = 3.0

# --- thresholds -------------------------------------------------------
# All recorded in the stored evidence, because a boolean derived from a
# threshold is only interpretable alongside the threshold.

#: Mean absolute frame difference (0..255 scale) above which consecutive
#: frames are treated as different shots.
CUT_DIFF = 18.0
#: Frame difference below which the camera is treated as static.
STATIC_DIFF = 2.0
#: Estimated global translation, in analysis-frame pixels per frame, above
#: which the camera is treated as moving.
MOTION_PX = 1.2
#: Audio RMS (0..1 scale) above which sound is treated as present.
SOUND_FLOOR = 0.01
#: Seconds within which sound must start to count as "immediate".
IMMEDIATE_S = 0.25
#: Edge-density score above which on-screen text is *suspected*.
#:
#: Calibrated against 12 openings judged by eye (see
#: docs/benchmarks/6a-hook-calibration.md): 10 of 12 genuinely carried
#: text, and no threshold separates them cleanly - a busy studio with no
#: text scored 0.165 while a real caption scored 0.047. At this value the
#: proxy is right 9 times in 12.
#:
#: The honest conclusion from that calibration is that the BOOLEAN is close
#: to worthless in this corpus, because the base rate of on-screen text is
#: about 83% and the detector cannot beat it. `text_region_score` is kept
#: as a continuous measure of how much strong-stroke content the frame
#: carries; the boolean is reported with its measured error rate attached.
TEXT_SCORE = 0.067
#: Measured on the calibration set, carried with the boolean so nobody has
#: to go looking for it.
TEXT_PROXY_PRECISION = 0.89
TEXT_PROXY_RECALL = 0.80
TEXT_PROXY_N = 12


class ExtractionError(RuntimeError):
    """The file could not be measured, and why."""


@dataclass
class Measurement:
    """One measured quantity, with everything needed to judge it.

    `uncertainty` is deliberately not one thing: for a count it is the
    sensitivity of that count to the threshold, for a time it is the
    sampling resolution, and for a proxy it is a stated confidence. What it
    means is recorded in `uncertainty_kind` rather than left to be guessed.
    """

    name: str
    value: float | int | bool | str | None
    #: deterministic | proxy | unavailable
    method: str
    unit: str | None = None
    #: Seconds [start, end] this was measured over.
    interval: tuple[float, float] | None = None
    uncertainty: float | None = None
    #: resolution | threshold_margin | confidence | none
    uncertainty_kind: str | None = None
    #: Present when `method == "unavailable"`.
    unavailable_reason: str | None = None
    #: The thresholds and parameters that produced the value.
    params: dict = field(default_factory=dict)
    note: str | None = None


@dataclass
class HookFeatures:
    video_path: str
    method_version: str
    hook_seconds: float
    #: Source properties, so a measurement can be judged against the file.
    duration_s: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    has_audio: bool = False
    frames_analysed: int = 0
    audio_samples_analysed: int = 0
    measurements: list[Measurement] = field(default_factory=list)
    #: Non-fatal problems: a missing audio track, a video shorter than the
    #: window. Kept rather than raised, because a 2-second video is a valid
    #: thing to have measured badly-defined features on, and the caller
    #: needs to know which.
    warnings: list[str] = field(default_factory=list)
    extract_seconds: float | None = None

    def get(self, name: str) -> Measurement | None:
        for m in self.measurements:
            if m.name == name:
                return m
        return None

    def values(self) -> dict:
        return {m.name: m.value for m in self.measurements}

    def as_dict(self) -> dict:
        d = asdict(self)
        for m in d["measurements"]:
            if m.get("interval") is not None:
                m["interval"] = list(m["interval"])
        return d


# ---------------------------------------------------------------------------
# decode
# ---------------------------------------------------------------------------

def _tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise ExtractionError(f"{name} is not on PATH; cannot measure video")
    return path


def probe(path: str | Path) -> dict:
    """Source properties. Refuses anything without a real video stream."""
    out = subprocess.run(
        [_tool("ffprobe"), "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise ExtractionError(f"ffprobe failed: {out.stderr.strip()[:200]}")
    try:
        data = json.loads(out.stdout)
    except ValueError as exc:
        raise ExtractionError(f"ffprobe returned no JSON: {exc}") from exc

    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise ExtractionError("no video stream")

    duration = None
    for src in (video.get("duration"), (data.get("format") or {}).get("duration")):
        try:
            duration = float(src)
            break
        except (TypeError, ValueError):
            continue

    fps = None
    rate = video.get("avg_frame_rate") or video.get("r_frame_rate") or ""
    if "/" in rate:
        num, _, den = rate.partition("/")
        try:
            fps = float(num) / float(den) if float(den) else None
        except (TypeError, ValueError, ZeroDivisionError):
            fps = None

    return {
        "duration_s": duration,
        "width": int(video.get("width") or 0) or None,
        "height": int(video.get("height") or 0) or None,
        "fps": fps,
        "has_audio": audio is not None,
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name") if audio else None,
    }


def decode_frames(path: str | Path, *, start: float, seconds: float,
                  fps: float, width: int, height: int) -> np.ndarray:
    """Decode a window to an (N, H, W, 3) uint8 array."""
    cmd = [
        _tool("ffmpeg"), "-v", "error", "-nostdin",
        "-ss", f"{start:.3f}", "-t", f"{seconds:.3f}", "-i", str(path),
        "-vf", f"fps={fps},scale={width}:{height}",
        "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
    ]
    out = subprocess.run(cmd, capture_output=True, timeout=300)
    if out.returncode != 0 and not out.stdout:
        raise ExtractionError(
            f"frame decode failed: {out.stderr.decode(errors='replace')[:200]}")
    frame_bytes = width * height * 3
    n = len(out.stdout) // frame_bytes
    if n == 0:
        return np.zeros((0, height, width, 3), dtype=np.uint8)
    return np.frombuffer(
        out.stdout[:n * frame_bytes], dtype=np.uint8
    ).reshape(n, height, width, 3)


def decode_audio(path: str | Path, *, start: float, seconds: float,
                 sr: int = AUDIO_SR) -> np.ndarray:
    """Decode a window to mono float32 in [-1, 1]. Empty if no audio."""
    cmd = [
        _tool("ffmpeg"), "-v", "error", "-nostdin",
        "-ss", f"{start:.3f}", "-t", f"{seconds:.3f}", "-i", str(path),
        "-vn", "-ac", "1", "-ar", str(sr),
        "-f", "s16le", "-acodec", "pcm_s16le", "-",
    ]
    out = subprocess.run(cmd, capture_output=True, timeout=300)
    if out.returncode != 0 and not out.stdout:
        return np.zeros(0, dtype=np.float32)
    raw = np.frombuffer(out.stdout, dtype="<i2")
    if raw.size == 0:
        return np.zeros(0, dtype=np.float32)
    return (raw.astype(np.float32) / 32768.0)


# ---------------------------------------------------------------------------
# visual measurement
# ---------------------------------------------------------------------------

def _gray(frames: np.ndarray) -> np.ndarray:
    """Rec. 601 luma, float32."""
    return (frames[..., 0] * 0.299 + frames[..., 1] * 0.587
            + frames[..., 2] * 0.114).astype(np.float32)


def _frame_diffs(gray: np.ndarray) -> np.ndarray:
    if gray.shape[0] < 2:
        return np.zeros(0, dtype=np.float32)
    return np.abs(np.diff(gray, axis=0)).mean(axis=(1, 2))


def _phase_shift(a: np.ndarray, b: np.ndarray) -> float:
    """Global translation between two frames, in pixels.

    Phase correlation via FFT: the cross-power spectrum's inverse transform
    peaks at the translation that best aligns the pair. Deterministic, and
    it needs nothing beyond numpy.

    Returns the magnitude only. Direction is not reported because a single
    number per frame pair is all the downstream "is the camera moving"
    question needs, and reporting a direction would invite reading it as a
    tracked motion path, which this is not.
    """
    if a.shape != b.shape or a.size == 0:
        return 0.0
    # Hann window, so the frame edges do not dominate the correlation.
    wy = np.hanning(a.shape[0])[:, None]
    wx = np.hanning(a.shape[1])[None, :]
    fa = np.fft.rfft2((a - a.mean()) * wy * wx)
    fb = np.fft.rfft2((b - b.mean()) * wy * wx)
    cross = fa * np.conj(fb)
    mag = np.abs(cross)
    mag[mag < 1e-8] = 1e-8
    corr = np.fft.irfft2(cross / mag, s=a.shape)
    peak = int(np.argmax(corr))
    dy, dx = np.unravel_index(peak, corr.shape)
    h, w = corr.shape
    if dy > h // 2:
        dy -= h
    if dx > w // 2:
        dx -= w
    return float(math.hypot(dx, dy))


def _edge_density(gray_frame: np.ndarray) -> float:
    gy, gx = np.gradient(gray_frame)
    mag = np.hypot(gx, gy)
    return float((mag > 24.0).mean())


def _text_region_score(gray_frame: np.ndarray) -> float:
    """A proxy for burned-in captions. NOT optical character recognition.

    Rendered text has a signature a gradient can see: dense, high-contrast,
    predominantly *vertical* edges (letter strokes) arranged in horizontal
    runs. This scores the strongest horizontal band on that signature.

    It cannot read anything, it cannot tell a caption from a logo or a busy
    piano keyboard, and it is reported as `proxy` with a confidence derived
    from how far it sits from the threshold. Actual text content needs OCR
    and is reported as unavailable until a model provides it.
    """
    gy, gx = np.gradient(gray_frame)
    vertical_strokes = (np.abs(gx) > 28.0) & (np.abs(gx) > np.abs(gy) * 1.6)
    h = vertical_strokes.shape[0]
    band = max(4, h // 12)
    best = 0.0
    for top in range(0, h - band + 1, max(1, band // 2)):
        best = max(best, float(vertical_strokes[top:top + band].mean()))
    return best


def _descriptor(frames: np.ndarray) -> np.ndarray:
    """A small, comparable summary of a window's look.

    A 4x4x(3 colour + 1 edge) grid. Enough to notice that the opening is a
    different scene from the middle; far too coarse to identify anything,
    which is the intent.
    """
    if frames.shape[0] == 0:
        return np.zeros(64, dtype=np.float32)
    mean_frame = frames.mean(axis=0)
    gray = (mean_frame[..., 0] * 0.299 + mean_frame[..., 1] * 0.587
            + mean_frame[..., 2] * 0.114)
    gy, gx = np.gradient(gray)
    edges = np.hypot(gx, gy)
    h, w = gray.shape
    out = []
    for by in range(4):
        for bx in range(4):
            ys = slice(by * h // 4, (by + 1) * h // 4)
            xs = slice(bx * w // 4, (bx + 1) * w // 4)
            cell = mean_frame[ys, xs]
            out.extend([cell[..., 0].mean(), cell[..., 1].mean(),
                        cell[..., 2].mean(), edges[ys, xs].mean()])
    v = np.asarray(out, dtype=np.float32)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


# ---------------------------------------------------------------------------
# audio measurement
# ---------------------------------------------------------------------------

def _frame_audio(x: np.ndarray) -> np.ndarray:
    """Strided framing, (frames, AUDIO_FRAME)."""
    if x.size < AUDIO_FRAME:
        return np.zeros((0, AUDIO_FRAME), dtype=np.float32)
    n = 1 + (x.size - AUDIO_FRAME) // AUDIO_HOP
    idx = np.arange(AUDIO_FRAME)[None, :] + AUDIO_HOP * np.arange(n)[:, None]
    return x[idx]


def _rms(frames: np.ndarray) -> np.ndarray:
    if frames.shape[0] == 0:
        return np.zeros(0, dtype=np.float32)
    return np.sqrt((frames ** 2).mean(axis=1) + 1e-12)


def _spectra(frames: np.ndarray) -> np.ndarray:
    if frames.shape[0] == 0:
        return np.zeros((0, AUDIO_FRAME // 2 + 1), dtype=np.float32)
    win = np.hanning(AUDIO_FRAME).astype(np.float32)
    return np.abs(np.fft.rfft(frames * win, axis=1)).astype(np.float32)


def _spectral_stats(frames: np.ndarray, spec: np.ndarray) -> dict:
    """Descriptive spectral statistics. NOT a speech detector.

    An earlier version of this module combined these three numbers into a
    `speech_likelihood` score and used it to gate transcription. Measured
    against Silero VAD and Whisper on 12 real openings from this corpus, it
    called speech on **12 of 12** when the true answer was **0 of 12**. It
    was not mis-tuned, it was measuring the wrong thing: sustained solo
    piano varies its spectral flatness and zero-crossing rate frame to
    frame just as much as speech does.

    The numbers are kept because they are real descriptive statistics of
    the opening audio and cost nothing. They are no longer allowed to
    decide anything. Speech presence comes from `shared/hookspeech.py`,
    which asks a VAD model.
    """
    if frames.shape[0] < 8 or spec.shape[0] < 8:
        return {}
    eps = 1e-10
    geo = np.exp(np.log(spec + eps).mean(axis=1))
    arith = spec.mean(axis=1) + eps
    flatness = geo / arith
    signs = np.sign(frames)
    zcr = np.mean(np.abs(np.diff(signs, axis=1)) > 0, axis=1)
    freqs = np.fft.rfftfreq(AUDIO_FRAME, d=1.0 / AUDIO_SR)
    voice = (freqs >= 300) & (freqs <= 3400)
    total = spec.sum(axis=1) + eps
    centroid = float((spec * freqs[None, :]).sum() / spec.sum())
    return {
        "spectral_flatness_mean": float(flatness.mean()),
        "spectral_flatness_sd": float(flatness.std()),
        "zero_crossing_rate_mean": float(zcr.mean()),
        "voice_band_ratio": float(np.mean(spec[:, voice].sum(axis=1) / total)),
        "spectral_centroid_hz": centroid,
    }


def _onsets(rms: np.ndarray) -> int:
    """Energy-rise events. A rough count of distinct attacks."""
    if rms.size < 5:
        return 0
    d = np.diff(rms)
    thresh = max(float(np.std(d)) * 1.8, 0.004)
    peaks = 0
    last = -10
    for i, val in enumerate(d):
        if val > thresh and i - last > 5:      # 50 ms refractory
            peaks += 1
            last = i
    return peaks


# ---------------------------------------------------------------------------
# the extractor
# ---------------------------------------------------------------------------

def _m(name, value, method, **kw) -> Measurement:
    return Measurement(name=name, value=value, method=method, **kw)


def _threshold_confidence(value: float, threshold: float,
                          scale: float) -> float:
    """How far a value sits from its threshold, as a 0..1 confidence.

    A value sitting exactly on the threshold is a coin flip and is reported
    as 0.5, not as a decision.
    """
    if scale <= 0:
        return 0.5
    return float(min(max(0.5 + (value - threshold) / (2 * scale), 0.0), 1.0))


def extract_hook(path: str | Path, *, hook_seconds: float = HOOK_SECONDS
                 ) -> HookFeatures:
    """Measure the opening window of one real video file.

    Never writes to or alters the source file.
    """
    import time
    started = time.monotonic()
    path = Path(path)
    if not path.exists():
        raise ExtractionError(f"no such file: {path}")

    info = probe(path)
    out = HookFeatures(video_path=str(path), method_version=METHOD_VERSION,
                       hook_seconds=hook_seconds, **{
                           k: info[k] for k in
                           ("duration_s", "width", "height", "fps",
                            "has_audio")})

    duration = info["duration_s"] or 0.0
    window = min(hook_seconds, duration) if duration else hook_seconds
    if duration and duration < hook_seconds:
        out.warnings.append(
            f"The video is {duration:.1f}s long, shorter than the "
            f"{hook_seconds:.0f}s hook window. Opening features describe "
            f"the whole video, and novelty-versus-later cannot be computed.")

    # ---- frames ------------------------------------------------------
    aw = ANALYSIS_WIDTH
    ah = max(2, int(round(aw * (info["height"] or 16) / (info["width"] or 9))))
    ah += ah % 2
    frames = decode_frames(path, start=0.0, seconds=window, fps=FRAME_FPS,
                           width=aw, height=ah)
    out.frames_analysed = int(frames.shape[0])
    if frames.shape[0] == 0:
        raise ExtractionError("decoded zero frames from the opening window")

    gray = _gray(frames)
    diffs = _frame_diffs(gray)
    iv = (0.0, float(window))

    # cuts
    cut_idx = np.nonzero(diffs > CUT_DIFF)[0]
    cut_times = [round(float((i + 1) / FRAME_FPS), 2) for i in cut_idx]
    # How many cuts a +/-25% threshold move would add or remove: the
    # sensitivity of the count, which is the honest uncertainty on it.
    loose = int((diffs > CUT_DIFF * 0.75).sum())
    tight = int((diffs > CUT_DIFF * 1.25).sum())
    out.measurements += [
        _m("cut_count", int(cut_idx.size), "deterministic", unit="cuts",
           interval=iv, uncertainty=float(max(loose - int(cut_idx.size),
                                              int(cut_idx.size) - tight)),
           uncertainty_kind="threshold_margin",
           params={"mean_abs_frame_diff_threshold": CUT_DIFF,
                   "fps": FRAME_FPS},
           note="Hard cuts only. A dissolve or a whip pan is not counted "
                "as a cut and may inflate the motion figures instead."),
        _m("cut_times_s", cut_times, "deterministic", unit="s", interval=iv,
           uncertainty=round(1.0 / FRAME_FPS, 3),
           uncertainty_kind="resolution",
           params={"fps": FRAME_FPS}),
        _m("mean_abs_frame_diff", round(float(diffs.mean()), 3)
           if diffs.size else 0.0, "deterministic", unit="luma 0-255",
           interval=iv, uncertainty_kind="none"),
    ]

    # camera motion, measured only across non-cut pairs: a cut is not
    # camera movement, and including it would make every edited opening
    # look like a handheld one.
    shifts = []
    for i in range(frames.shape[0] - 1):
        if diffs[i] > CUT_DIFF:
            continue
        shifts.append(_phase_shift(gray[i], gray[i + 1]))
    motion = float(np.median(shifts)) if shifts else 0.0
    motion_scaled = motion * (1.0 if not info["width"]
                              else info["width"] / aw)
    if not shifts:
        motion_class, motion_conf = "unknown", 0.0
    elif motion <= STATIC_DIFF * 0.5:
        motion_class = "static"
        motion_conf = _threshold_confidence(MOTION_PX - motion, 0.0, MOTION_PX)
    elif motion >= MOTION_PX:
        motion_class = "moving"
        motion_conf = _threshold_confidence(motion, MOTION_PX, MOTION_PX)
    else:
        motion_class = "slight"
        motion_conf = 0.5
    out.measurements += [
        _m("camera_motion_px_per_frame", round(motion, 3), "deterministic",
           unit="analysis px", interval=iv,
           uncertainty=0.5, uncertainty_kind="resolution",
           params={"analysis_width": aw, "fps": FRAME_FPS,
                   "excluded_cut_pairs": int((diffs > CUT_DIFF).sum())},
           note="Global translation by FFT phase correlation, median over "
                "frame pairs that are not cuts. A zoom or a rotation is "
                "not translation and is under-reported by this."),
        _m("camera_motion_source_px", round(motion_scaled, 2),
           "deterministic", unit="source px", interval=iv),
        _m("camera_motion_class", motion_class, "proxy", interval=iv,
           uncertainty=round(motion_conf, 2),
           uncertainty_kind="confidence",
           params={"static_below": STATIC_DIFF * 0.5,
                   "moving_at_or_above": MOTION_PX}),
        _m("scene_change_count", int((diffs > CUT_DIFF).sum()),
           "deterministic", unit="changes", interval=iv,
           params={"threshold": CUT_DIFF}),
    ]

    # composition
    first = frames[0].astype(np.float32)
    bright = float(gray.mean())
    contrast = float(gray.std())
    mx = frames.max(axis=3).astype(np.float32)
    mn = frames.min(axis=3).astype(np.float32)
    sat = float(np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0).mean())
    edge = float(np.mean([_edge_density(g) for g in gray[:: max(1, len(gray) // 6)]]))
    text_scores = [_text_region_score(g)
                   for g in gray[:: max(1, len(gray) // 6)]]
    text_score = float(max(text_scores)) if text_scores else 0.0

    out.measurements += [
        _m("brightness_mean", round(bright, 2), "deterministic",
           unit="luma 0-255", interval=iv),
        _m("contrast_rms", round(contrast, 2), "deterministic",
           unit="luma sd", interval=iv),
        _m("saturation_mean", round(sat, 4), "deterministic",
           unit="0-1", interval=iv),
        _m("edge_density", round(edge, 4), "deterministic", unit="0-1",
           interval=iv, params={"gradient_threshold": 24.0},
           note="Fraction of pixels on a strong gradient. A busy frame "
                "scores high whether it is busy with text, a keyboard or a "
                "patterned wall."),
        _m("text_region_score", round(text_score, 4), "proxy", unit="0-1",
           interval=iv, uncertainty=round(_threshold_confidence(
               text_score, TEXT_SCORE, TEXT_SCORE), 2),
           uncertainty_kind="confidence",
           params={"suspect_at": TEXT_SCORE},
           note="Dense vertical strokes in a horizontal band. This is NOT "
                "OCR: it cannot read text and cannot distinguish a caption "
                "from a logo or a piano keyboard."),
        _m("text_present_estimate", bool(text_score > TEXT_SCORE), "proxy",
           interval=iv,
           uncertainty=round(_threshold_confidence(
               text_score, TEXT_SCORE, TEXT_SCORE), 2),
           uncertainty_kind="confidence",
           params={"threshold": TEXT_SCORE,
                   "measured_precision": TEXT_PROXY_PRECISION,
                   "measured_recall": TEXT_PROXY_RECALL,
                   "calibration_n": TEXT_PROXY_N},
           note=f"A suspicion from an edge proxy, not a reading. Measured "
                f"against {TEXT_PROXY_N} openings judged by eye: precision "
                f"{TEXT_PROXY_PRECISION}, recall {TEXT_PROXY_RECALL}. The "
                f"base rate of on-screen text in this corpus is about 83%, "
                f"so this boolean carries very little information and "
                f"should not be used as a predictor without human "
                f"confirmation."),
    ]

    # ---- audio -------------------------------------------------------
    if not info["has_audio"]:
        out.warnings.append("The file has no audio stream.")
        for name in ("first_sound_at_s", "audio_starts_immediately",
                     "audio_rms_mean", "audio_rms_peak", "audio_onset_count",
                     "audio_attack_time_s", "spectral_centroid_hz",
                     "spectral_flatness_mean", "voice_band_ratio",
                     "speech_present_estimate", "spoken_words"):
            out.measurements.append(_m(
                name, None, "unavailable",
                unavailable_reason="the file has no audio stream"))
    else:
        audio = decode_audio(path, start=0.0, seconds=window)
        out.audio_samples_analysed = int(audio.size)
        af = _frame_audio(audio)
        rms = _rms(af)
        spec = _spectra(af)
        hop_s = AUDIO_HOP / AUDIO_SR

        loud = np.nonzero(rms > SOUND_FLOOR)[0]
        first_sound = float(loud[0] * hop_s) if loud.size else None
        peak = float(rms.max()) if rms.size else 0.0
        attack = None
        if rms.size and peak > 0:
            reach = np.nonzero(rms >= peak * 0.8)[0]
            attack = float(reach[0] * hop_s) if reach.size else None
        stats = _spectral_stats(af, spec)

        out.measurements += [
            _m("first_sound_at_s",
               round(first_sound, 3) if first_sound is not None else None,
               "deterministic", unit="s", interval=iv,
               uncertainty=round(hop_s, 4), uncertainty_kind="resolution",
               params={"rms_floor": SOUND_FLOOR},
               unavailable_reason=(None if first_sound is not None
                                   else "the window is silent above the "
                                        "RMS floor")),
            _m("audio_starts_immediately",
               bool(first_sound is not None and first_sound <= IMMEDIATE_S),
               "deterministic", interval=iv,
               params={"immediate_within_s": IMMEDIATE_S,
                       "rms_floor": SOUND_FLOOR}),
            _m("audio_rms_mean", round(float(rms.mean()), 5)
               if rms.size else 0.0, "deterministic", unit="0-1",
               interval=iv),
            _m("audio_rms_peak", round(peak, 5), "deterministic", unit="0-1",
               interval=iv),
            _m("audio_onset_count", _onsets(rms), "deterministic",
               unit="onsets", interval=iv,
               uncertainty_kind="threshold_margin",
               params={"refractory_frames": 5},
               note="Energy rises, which for solo piano approximates note "
                    "attacks and for speech approximates syllables."),
            _m("audio_attack_time_s",
               round(attack, 3) if attack is not None else None,
               "deterministic", unit="s", interval=iv,
               uncertainty=round(hop_s, 4), uncertainty_kind="resolution",
               params={"fraction_of_peak": 0.8}),
            _m("spectral_centroid_hz",
               round(stats.get("spectral_centroid_hz", 0.0), 1),
               "deterministic", unit="Hz", interval=iv),
            _m("spectral_flatness_mean",
               round(stats.get("spectral_flatness_mean", 0.0), 5),
               "deterministic", unit="0-1", interval=iv),
            _m("voice_band_ratio",
               round(stats.get("voice_band_ratio", 0.0), 4),
               "deterministic", unit="0-1", interval=iv,
               note="Share of spectral energy in 300-3400 Hz. A "
                    "descriptive statistic. It does NOT indicate speech: "
                    "measured against VAD on 12 real openings, a detector "
                    "built on this and two similar statistics was wrong "
                    "12 times out of 12."),
            _m("speech_present_estimate", None, "unavailable",
               interval=iv,
               unavailable_reason=(
                   "needs a VAD model. The deterministic statistics here "
                   "cannot separate solo piano from speech - measured, see "
                   "docs/benchmarks/6a-hook-calibration.md. "
                   "`shared/hookspeech.py` answers this.")),
            _m("spoken_words", None, "unavailable", interval=iv,
               unavailable_reason=(
                   "needs VAD-gated speech recognition. Whisper "
                   "hallucinates 'you' and 'Thank you' on music-only audio "
                   "(reproduced on this corpus), so a transcript is only "
                   "stored when a VAD model finds speech AND the result "
                   "survives a hallucination filter.")),
        ]

    # ---- novelty against the rest of the video -----------------------
    if duration and duration >= COMPARISON_START_S + 1.0:
        later_s = min(COMPARISON_SECONDS,
                      max(1.0, duration - COMPARISON_START_S))
        later = decode_frames(path, start=COMPARISON_START_S, seconds=later_s,
                              fps=FRAME_FPS, width=aw, height=ah)
        if later.shape[0]:
            d0, d1 = _descriptor(frames), _descriptor(later)
            vis_novelty = float(np.linalg.norm(d0 - d1))
            out.measurements.append(_m(
                "visual_novelty_vs_later", round(vis_novelty, 4),
                "deterministic", unit="descriptor distance 0-2",
                interval=(0.0, float(COMPARISON_START_S + later_s)),
                params={"comparison_start_s": COMPARISON_START_S,
                        "comparison_seconds": round(later_s, 2),
                        "descriptor": "4x4 RGB+edge grid, L2-normalised"},
                note="How different the opening looks from a later window. "
                     "High means the opening is not representative of the "
                     "video, not that it is good."))
        if info["has_audio"]:
            la = decode_audio(path, start=COMPARISON_START_S,
                              seconds=later_s)
            lf = _frame_audio(la)
            if lf.shape[0]:
                s0 = _spectra(_frame_audio(
                    decode_audio(path, start=0.0, seconds=window)))
                s1 = _spectra(lf)
                if s0.shape[0] and s1.shape[0]:
                    m0 = s0.mean(axis=0)
                    m1 = s1.mean(axis=0)
                    m0 = m0 / (np.linalg.norm(m0) + 1e-9)
                    m1 = m1 / (np.linalg.norm(m1) + 1e-9)
                    out.measurements.append(_m(
                        "audio_novelty_vs_later",
                        round(float(np.linalg.norm(m0 - m1)), 4),
                        "deterministic", unit="spectrum distance 0-2",
                        interval=(0.0, float(COMPARISON_START_S + later_s)),
                        params={"comparison_start_s": COMPARISON_START_S}))
    else:
        out.warnings.append(
            "Too short to compare the opening against a later window, so "
            "novelty is not measured.")

    # ---- what this pass deliberately cannot say ----------------------
    for name, reason in (
        ("on_screen_text_content",
         "needs OCR. `text_region_score` suspects that text exists; it "
         "cannot read it."),
        ("performer_visible", "needs a vision model."),
        ("piano_visible", "needs a vision model."),
        ("hands_visible", "needs a vision model."),
        ("face_visible", "needs a vision model."),
        ("framing_class",
         "needs a vision model. `edge_density` and the composition figures "
         "describe the frame without classifying the shot."),
        ("emotional_presentation",
         "needs a vision-language model, and is an interpretation rather "
         "than a measurement even then."),
    ):
        out.measurements.append(_m(name, None, "unavailable",
                                   unavailable_reason=reason))

    out.extract_seconds = round(time.monotonic() - started, 3)
    return out
