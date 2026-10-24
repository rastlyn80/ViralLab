"""Robust lightweight audio fingerprinting.

Replaces the 32-bin loudness envelope, which worked for the first pass but
is fragile in exactly the ways this archive needs:

  * it compares *absolute* positions, so a 1-second trim misaligns everything
  * it describes loudness only, so two different piano pieces with similar
    dynamics look alike
  * a different opening hook shifts every bucket

This is a sub-Chromaprint design — the same idea, far smaller:

  1. decode to 11025 Hz mono (piano energy lives well below 5 kHz)
  2. frame at ~0.093 s with 50% overlap
  3. FFT, fold into 16 log-spaced bands across 80-5000 Hz
  4. emit one 16-bit word per frame, each bit the SIGN of a band-energy
     change relative to the previous frame

Signs of *changes* are what make it robust: absolute level drops out, so
volume normalisation and encoding gain are free. The result is a sequence,
so alignment is a sliding comparison — a trim, a different hook or a
shortened clip costs only the frames it removes.

numpy only; no ML, no model files, no training.
"""

from __future__ import annotations

import array
import os
import subprocess
from pathlib import Path

import numpy as np

FFMPEG = os.environ.get("FFMPEG", "ffmpeg")

SAMPLE_RATE = 11025
FRAME = 1024                      # ~92.9 ms
HOP = FRAME // 2                  # 50% overlap
BANDS = 16
FREQ_LO, FREQ_HI = 80.0, 5000.0
#: Per-word Hamming distance below which two frames are "the same moment".
#: 16-bit words, so 4 is a quarter of the bits.
FRAME_MATCH_BITS = 4
#: A match needs at least this many aligned frames, so two short silences
#: cannot score 1.0 against each other. ~2 s.
MIN_ALIGNED_FRAMES = 43
#: And the aligned span must cover at least this fraction of the SHORTER
#: fingerprint.
#:
#: Both floors exist because the score is a matched fraction *of the aligned
#: span*, which on its own rewards finding a tiny agreeable window. With a
#: 12-frame floor, `Billie Eilish.mp4` and `Unicef.mp4` - two unrelated
#: files of 517 and 560 frames - scored **1.000 on 22 aligned frames**, 4% of
#: either. Chained through single-linkage grouping, spurious links like that
#: merged 33 unrelated performances into one.
#:
#: Measuring coverage against the shorter side is what keeps genuine partial
#: overlaps intact: a 15 s Reel cut from a 60 s master covers 100% of the
#: Reel, so trims and excerpts still match. What it rejects is a pair whose
#: shared stretch is small relative to *both* files, which is weak evidence
#: however well those few frames agree.
MIN_COVERAGE = 0.5


def _band_edges() -> np.ndarray:
    edges = np.logspace(np.log10(FREQ_LO), np.log10(FREQ_HI), BANDS + 1)
    return (edges / (SAMPLE_RATE / 2) * (FRAME // 2)).astype(int)


_EDGES = _band_edges()


def _band_matrix() -> np.ndarray:
    """0/1 weights folding FFT bins into bands.

    A matrix rather than a per-frame loop: this runs over every master and
    every published clip, and a single matmul is roughly two orders of
    magnitude faster than summing 16 slices per frame in Python. The
    `max(lo+1, hi)` keeps the lowest bands non-empty, where log spacing
    would otherwise collapse several bands onto the same FFT bin.
    """
    w = np.zeros((BANDS, FRAME // 2 + 1), dtype=np.float32)
    for b in range(BANDS):
        lo, hi = _EDGES[b], max(_EDGES[b] + 1, _EDGES[b + 1])
        w[b, lo:hi] = 1.0
    return w


_BANDW = _band_matrix()


def decode_mono(path: Path, max_seconds: float | None = None
                ) -> np.ndarray | None:
    """Decode to mono float32 at SAMPLE_RATE via ffmpeg."""
    cmd = [FFMPEG, "-v", "error"]
    if max_seconds:
        cmd += ["-t", f"{max_seconds:.2f}"]
    cmd += ["-i", str(path), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
            "-f", "s16le", "-"]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=240)
    except (OSError, subprocess.TimeoutExpired):
        return None
    raw = out.stdout
    if not raw or len(raw) < FRAME * 4:
        return None
    pcm = array.array("h")
    pcm.frombytes(raw[: len(raw) - (len(raw) % 2)])
    return np.asarray(pcm, dtype=np.float32) / 32768.0


def fingerprint_samples(samples: np.ndarray) -> list[int]:
    """Sequence of 16-bit words, one per frame."""
    n_frames = 1 + (len(samples) - FRAME) // HOP
    if n_frames < 2:
        return []
    window = np.hanning(FRAME).astype(np.float32)

    # One strided view over all frames, one batched FFT, one matmul.
    frames = np.lib.stride_tricks.as_strided(
        samples, shape=(n_frames, FRAME),
        strides=(samples.strides[0] * HOP, samples.strides[0]))
    spec = np.abs(np.fft.rfft(frames * window, axis=1)).astype(np.float32) ** 2
    energies = spec @ _BANDW.T

    # Log domain: a multiplicative gain becomes an additive offset, which
    # the differencing below then removes entirely.
    energies = np.log1p(energies * 1e6)

    # One bit per band, MSB = lowest band, matching the loop it replaces.
    bits = (np.diff(energies, axis=0) > 0)
    weights = (1 << np.arange(BANDS - 1, -1, -1)).astype(np.uint32)
    return (bits * weights).sum(axis=1).astype(np.uint32).tolist()


#: Number of sub-frame phases stored. A trim of an arbitrary number of
#: milliseconds is NOT a whole number of hops, so a single-phase fingerprint
#: compares windows that straddle different audio and scores ~0.5 even when
#: perfectly aligned. Benchmarking showed exactly that: offset_1s scored
#: 0.448 while reporting the correct -1.02 s offset. Storing a second phase
#: halves the worst-case misalignment and recovers the score.
PHASES = 2


def fingerprint(path: Path, max_seconds: float | None = None
                ) -> dict | None:
    samples = decode_mono(path, max_seconds)
    if samples is None:
        return None
    phases = []
    for p in range(PHASES):
        shifted = samples[p * (HOP // PHASES):]
        words = fingerprint_samples(shifted)
        if len(words) >= MIN_ALIGNED_FRAMES:
            phases.append([f"{w:04x}" for w in words])
    if not phases:
        return None
    return {"v": 3, "rate": SAMPLE_RATE, "hop": HOP, "phases": len(phases),
            "words": phases[0],              # primary, for compactness
            "alt": phases[1:],               # sub-frame variants
            "seconds": round(len(samples) / SAMPLE_RATE, 2)}


def load_words(fp: dict | None) -> np.ndarray | None:
    if not fp or not fp.get("words"):
        return None
    return np.array([int(w, 16) for w in fp["words"]], dtype=np.uint16)


_POPCOUNT = np.array([bin(i).count("1") for i in range(1 << 16)],
                     dtype=np.uint8)


def _all_phases(fp: dict | None) -> list[np.ndarray]:
    out = []
    primary = load_words(fp)
    if primary is not None:
        out.append(primary)
    for alt in (fp or {}).get("alt") or []:
        out.append(np.array([int(w, 16) for w in alt], dtype=np.uint16))
    return out


#: Seconds of audio used for a *screening* comparison. Identifying a
#: recording does not need the whole thing: the benchmark's sources run
#: 10-65 s and a 3-second head trim still scored 0.99. Capping the screening
#: pass keeps a full-corpus sweep to minutes instead of hours; the pairs that
#: survive are then re-scored at full length.
QUICK_SECONDS = 30.0


def _quick_frames() -> int:
    return int(QUICK_SECONDS * SAMPLE_RATE / HOP)


def compare(a: dict | None, b: dict | None, *,
            min_aligned: int = MIN_ALIGNED_FRAMES,
            quick: bool = False) -> dict:
    """Best sliding alignment between two fingerprints.

    Returns the matched fraction over the overlapping region, the offset in
    seconds, and how many frames actually aligned. Offset is reported
    because a nonzero one is itself evidence: it means the published cut
    starts later than the master, i.e. a different hook or a trim.
    """
    if quick:
        # Screening: primary phase only, truncated. Cheap and deliberately
        # permissive - it decides what to look at properly, not what matches.
        n = _quick_frames()
        pa, pb = load_words(a), load_words(b)
        if pa is None or pb is None:
            return {"score": 0.0, "offset_s": 0.0, "aligned_frames": 0,
                    "coverage": 0.0, "comparable": False}
        phases_a, phases_b = [pa[:n]], [pb[:n]]
    else:
        phases_a, phases_b = _all_phases(a), _all_phases(b)
    if not phases_a or not phases_b:
        return {"score": 0.0, "offset_s": 0.0, "aligned_frames": 0,
                "coverage": 0.0, "comparable": False}

    overall = {"score": 0.0, "offset_s": 0.0, "aligned_frames": 0,
               "coverage": 0.0, "comparable": True}
    # Try every phase pairing and keep the best: one of them lands close to
    # the true sub-frame alignment.
    for wa in phases_a:
        for wb in phases_b:
            r = _compare_single(wa, wb, min_aligned)
            if r["score"] > overall["score"]:
                overall = {**r, "comparable": True}
    return overall


def _compare_single(wa: np.ndarray, wb: np.ndarray, min_aligned: int) -> dict:
    if len(wa) < 2 or len(wb) < 2:
        return {"score": 0.0, "offset_s": 0.0, "aligned_frames": 0,
                "coverage": 0.0}

    short, long_ = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    flipped = len(wa) > len(wb)
    m = len(short)
    # Only alignments that actually cover the shorter side are considered, so
    # the search cannot win by finding a short agreeable window.
    min_aligned = max(min_aligned, int(MIN_COVERAGE * m))

    # Every alignment is evaluated in one pass instead of a Python loop over
    # offsets. The longer sequence is padded on both sides so each offset
    # yields a full-width window, and a parallel validity mask stops the
    # padding from counting as either a hit or part of the span - so partial
    # overlaps (a trimmed head, a shortened tail) still score on exactly the
    # frames they share.
    pad = m - min_aligned
    if pad < 0:
        return {"score": 0.0, "offset_s": 0.0, "aligned_frames": 0,
                "coverage": 0.0}
    padded = np.concatenate([np.zeros(pad, np.uint16), long_,
                             np.zeros(pad, np.uint16)])
    valid = np.concatenate([np.zeros(pad, bool),
                            np.ones(len(long_), bool),
                            np.zeros(pad, bool)])
    n_off = len(padded) - m + 1
    if n_off < 1:
        return {"score": 0.0, "offset_s": 0.0, "aligned_frames": 0,
                "coverage": 0.0}
    st = padded.strides[0]
    windows = np.lib.stride_tricks.as_strided(padded, (n_off, m), (st, st))
    vst = valid.strides[0]
    vmask = np.lib.stride_tricks.as_strided(valid, (n_off, m), (vst, vst))

    hit = (_POPCOUNT[np.bitwise_xor(windows, short)] <= FRAME_MATCH_BITS)
    spans = vmask.sum(axis=1)
    hits = (hit & vmask).sum(axis=1)
    ok = spans >= min_aligned
    if not ok.any():
        return {"score": 0.0, "offset_s": 0.0, "aligned_frames": 0,
                "coverage": 0.0}
    scores = np.where(ok, hits / np.maximum(spans, 1), 0.0)
    best = int(scores.argmax())
    best_off = best - pad

    hop_s = HOP / SAMPLE_RATE
    return {"score": round(float(scores[best]), 4),
            "offset_s": round((-best_off if flipped else best_off) * hop_s, 2),
            "aligned_frames": int(spans[best]),
            # Reported so a caller can see how much audio the score rests on.
            "coverage": round(float(spans[best]) / m, 3)}
