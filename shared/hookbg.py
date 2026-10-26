"""Background complexity of a video opening, measured without a model.

Phase 6C Part 5 asks whether `text_region_score` is associated with, among
other things, general visual density and background complexity. The vision
benchmark (docs/benchmarks/6c-vision-benchmark.md) established that the
local VLM cannot be trusted for object presence - it reports a piano in a
garden - so these two properties are measured from the pixels instead,
where they are fully determined and need no model at all.

**General visual density is already measured**: `edge_density` in
`hookfeat`. This module does not duplicate it.

**Background complexity is the harder half**, because "background" has to
be identified before its complexity can be measured. The decomposition used
here is temporal: across the hook frames, pixels that barely change are
background and pixels that change are subject. A pianist's hands move; the
bookshelf behind them does not.

That decomposition fails loudly rather than quietly. When the camera moves,
every pixel changes and no background can be identified; the module reports
`background_static_fraction` so the caller can see that, and marks the
complexity measures `unavailable` rather than returning a number computed
over four stray pixels.

What this cannot do: tell a busy background from a busy foreground held
still, distinguish a wall poster from a window, or say anything about shot
distance. It measures how much structure sits in the parts of the opening
that are not moving.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from shared.hookfeat import (ANALYSIS_WIDTH, FRAME_FPS, HOOK_SECONDS,
                             ExtractionError, Measurement, _gray, decode_frames,
                             probe)

logger = logging.getLogger("virallab.hookbg")

METHOD_VERSION = "hook-background-v1"

#: A pixel whose temporal standard deviation across the hook frames is below
#: this (8-bit grey levels) is treated as background. Sensor noise in these
#: Instagram re-encodes sits around 2-4 levels, so this is a little above
#: noise and well below real movement.
STATIC_STD = 6.0

#: Below this share of static pixels the foreground/background split has not
#: found a background, and the complexity measures are withheld.
MIN_STATIC_FRACTION = 0.15

#: Gradient magnitude counted as an edge. Same threshold as
#: `hookfeat._edge_density`, deliberately, so the numbers are comparable.
EDGE_MAG = 24.0

#: Grey levels are quantised into this many bins for the entropy measure.
ENTROPY_BINS = 16


@dataclass
class BackgroundFeatures:
    video_path: str
    method_version: str = METHOD_VERSION
    hook_seconds: float = HOOK_SECONDS
    frames_analysed: int = 0
    measurements: list[Measurement] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    extract_seconds: float | None = None

    def values(self) -> dict:
        return {m.name: m.value for m in self.measurements}


def _edge_density_masked(gray_frame: np.ndarray,
                         mask: np.ndarray) -> float | None:
    """Edge density within `mask` only.

    Gradients are taken on the whole frame and then sampled, rather than on
    a masked frame: masking first would manufacture a hard edge along the
    mask boundary and inflate exactly the number being measured.
    """
    if mask.sum() < 64:
        return None
    gy, gx = np.gradient(gray_frame)
    mag = np.hypot(gx, gy)
    return float((mag[mask] > EDGE_MAG).mean())


def _grey_entropy(gray_frame: np.ndarray, mask: np.ndarray) -> float | None:
    """Shannon entropy of the quantised grey histogram, in bits.

    Edges and entropy answer different questions. A smooth studio backdrop
    lit with a gradient has near-zero edge density but several distinct grey
    levels; a black curtain has neither. Reporting both separates "lots of
    stuff" from "lots of tone".
    """
    if mask.sum() < 64:
        return None
    vals = gray_frame[mask]
    hist, _ = np.histogram(vals, bins=ENTROPY_BINS, range=(0.0, 255.0))
    total = hist.sum()
    if total <= 0:
        return None
    p = hist[hist > 0] / total
    return float(-(p * np.log2(p)).sum())


def extract_background(path: str | Path, *,
                       hook_seconds: float = HOOK_SECONDS
                       ) -> BackgroundFeatures:
    """Measure background complexity over the opening of one video."""
    started = time.monotonic()
    path = Path(path)
    out = BackgroundFeatures(video_path=str(path), hook_seconds=hook_seconds)

    info = probe(path)
    # Same analysis geometry as hookfeat, so `background_edge_density` and
    # `edge_density` are measured on identically-scaled pixels and can be
    # compared directly.
    aw = ANALYSIS_WIDTH
    ah = max(2, int(round(aw * (info["height"] or 16) / (info["width"] or 9))))
    ah += ah % 2
    frames = decode_frames(path, start=0.0, seconds=hook_seconds,
                           fps=FRAME_FPS, width=aw, height=ah)
    if frames.shape[0] < 2:
        raise ExtractionError(
            f"need at least 2 frames to separate background from subject, "
            f"decoded {frames.shape[0]} from {path.name}")
    out.frames_analysed = int(frames.shape[0])
    if info.get("duration_s") and info["duration_s"] < hook_seconds:
        out.warnings.append(
            f"video is {info['duration_s']:.1f}s, shorter than the "
            f"{hook_seconds}s hook window")

    gray = _gray(frames).astype(np.float32)
    temporal_std = gray.std(axis=0)
    static = temporal_std < STATIC_STD
    static_fraction = float(static.mean())
    moving = ~static

    interval = (0.0, float(hook_seconds))
    common = {"interval": interval,
              "params": {"static_std": STATIC_STD, "edge_mag": EDGE_MAG,
                         "frames": out.frames_analysed,
                         "frame_fps": FRAME_FPS,
                         "analysis_width": ANALYSIS_WIDTH}}

    out.measurements.append(Measurement(
        name="background_static_fraction", value=round(static_fraction, 4),
        method="deterministic", unit="fraction",
        uncertainty=float(round(temporal_std.std() / 255.0, 4)),
        uncertainty_kind="threshold_margin",
        note="Share of the frame that barely changes across the hook. Low "
             "values mean the camera is moving and no background could be "
             "identified, which is why the complexity measures below are "
             "withheld under "
             f"{MIN_STATIC_FRACTION}.",
        **common))

    # The reference frame is the temporal median: it removes a hand passing
    # through without blurring the structure that stays put.
    reference = np.median(gray, axis=0)

    if static_fraction < MIN_STATIC_FRACTION:
        reason = (f"only {static_fraction:.1%} of the frame is static, below "
                  f"the {MIN_STATIC_FRACTION:.0%} needed to identify a "
                  f"background; the opening is probably a moving camera or a "
                  f"full-frame cut")
        out.warnings.append(reason)
        for name in ("background_edge_density", "background_grey_entropy",
                     "subject_edge_density", "background_subject_edge_ratio"):
            out.measurements.append(Measurement(
                name=name, value=None, method="unavailable",
                unavailable_reason=reason, **common))
        out.extract_seconds = round(time.monotonic() - started, 3)
        return out

    bg_edges = _edge_density_masked(reference, static)
    fg_edges = _edge_density_masked(reference, moving)
    bg_entropy = _grey_entropy(reference, static)

    out.measurements.append(Measurement(
        name="background_edge_density", value=round(bg_edges, 5),
        method="deterministic", unit="fraction",
        note="Edge density over the static part of the opening only. "
             "Comparable with hookfeat's whole-frame `edge_density`, which "
             "uses the same gradient threshold.",
        **common))
    out.measurements.append(Measurement(
        name="background_grey_entropy", value=round(bg_entropy, 4),
        method="deterministic", unit="bits",
        note=f"Shannon entropy of the static region's grey histogram over "
             f"{ENTROPY_BINS} bins. Separates tonal variety from edge count.",
        **common))

    if fg_edges is None:
        out.measurements.append(Measurement(
            name="subject_edge_density", value=None, method="unavailable",
            unavailable_reason="nothing in the opening moved enough to be "
                               "separated as subject",
            **common))
        out.measurements.append(Measurement(
            name="background_subject_edge_ratio", value=None,
            method="unavailable",
            unavailable_reason="no subject region to compare against",
            **common))
    else:
        out.measurements.append(Measurement(
            name="subject_edge_density", value=round(fg_edges, 5),
            method="deterministic", unit="fraction",
            note="Edge density over the moving part of the opening. Included "
                 "because background complexity only means something "
                 "relative to what is in front of it.",
            **common))
        ratio = (round(bg_edges / fg_edges, 4) if fg_edges > 1e-6 else None)
        out.measurements.append(Measurement(
            name="background_subject_edge_ratio", value=ratio,
            method="deterministic" if ratio is not None else "unavailable",
            unavailable_reason=(None if ratio is not None else
                                "the subject region has no measurable edges"),
            note="Above 1 means the background carries more structure than "
                 "the subject does.",
            **common))

    out.extract_seconds = round(time.monotonic() - started, 3)
    return out
