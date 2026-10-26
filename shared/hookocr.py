"""Real optical character recognition over a video's opening window.

Replaces `hookfeat.text_region_score`, which was an edge-density proxy that
could not read anything and could not tell a caption from a piano keyboard.
Measured on this corpus the proxy scored precision 0.89 / recall 0.80
against an 83% base rate, which is barely better than guessing "yes"
(docs/benchmarks/6a-hook-calibration.md). The proxy is kept as a cheap
continuous signal; this module is what actually answers the question.

**Engine.** RapidOCR on onnxruntime. Chosen because onnxruntime was already
present as a faster-whisper dependency, the models are a few tens of MB, it
runs on CPU where the media already lives, and it needs no torch - so the
RTX 2070 stays free for the vision-language work that genuinely needs VRAM.

**OCR output is not ground truth.** It mis-reads stylised fonts, it finds
text in sheet music and keyboard edges, and it is confident while doing so.
Every detection carries the engine's own score; every aggregate carries the
frame count it came from; and a frame that could not be processed is
recorded as unavailable rather than as "no text".

**Temporal sampling matters.** A caption that appears at 2.4 s is a
different creative choice from one on screen at 0.0 s, and one frame cannot
tell them apart. Frames are sampled across the window and each detection
keeps the timestamps it was seen at, so first-appearance, duration and
mid-hook changes are measured rather than assumed.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger("virallab.hookocr")

METHOD_VERSION = "hook-ocr-v1"
ENGINE = "rapidocr-onnxruntime"

#: Frames per second sampled for OCR inside the window. Lower than the
#: deterministic pass: OCR is ~100x the cost per frame, and text that
#: appears for less than 400 ms is not a caption anyone reads.
OCR_FPS = 2.5
#: Frames are OCR'd at this width. Text recognition genuinely needs pixels,
#: unlike the global statistics in `hookfeat`, so this is much larger than
#: that module's 160.
OCR_WIDTH = 540

#: Below this engine score a detection is kept but marked low-confidence
#: and excluded from the aggregate word list.
MIN_SCORE = 0.55
#: Detections shorter than this are almost always artefacts of keyboard
#: edges or stave lines rather than words.
MIN_CHARS = 2
#: Two detections are "the same element" when their boxes overlap this much,
#: which is how a caption held across frames is counted once.
IOU_SAME = 0.45

#: Strings the engine produces from musical notation and keyboard edges.
#: Matched as whole detections only.
NOISE_TOKENS = frozenset({
    "|", "||", "|||", "-", "--", "_", "__", "~", ".", "..", "...",
    "i", "ii", "l", "ll", "lll", "1", "11", "111", "o", "0", "oo",
})

_WS = re.compile(r"\s+")


class OcrUnavailable(RuntimeError):
    """OCR could not run, and why."""


_engine = None


def _load():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR
        logger.info("loading %s", ENGINE)
        _engine = RapidOCR()
    return _engine


@dataclass
class Detection:
    """One piece of text the engine found, in one frame."""

    text: str
    score: float
    #: (x0, y0, x1, y1) in fractions of frame width/height, so a box is
    #: comparable across the different resolutions in this corpus.
    box: tuple[float, float, float, float]
    at_s: float
    #: Box height as a fraction of frame height. The usable proxy for "how
    #: big is this text", which matters more than absolute pixels.
    height_frac: float


@dataclass
class TextElement:
    """A detection tracked across frames: one caption, not one sighting."""

    text: str
    first_seen_s: float
    last_seen_s: float
    frames_seen: int
    mean_score: float
    box: tuple[float, float, float, float]
    height_frac: float
    #: All spellings the engine produced for this element across frames.
    #: Disagreement between them is itself an uncertainty signal.
    variants: list[str] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return round(self.last_seen_s - self.first_seen_s, 2)


@dataclass
class OcrResult:
    method_version: str = METHOD_VERSION
    engine: str = ENGINE
    frames_processed: int = 0
    frames_failed: int = 0
    hook_seconds: float = 3.0
    detections: list[Detection] = field(default_factory=list)
    elements: list[TextElement] = field(default_factory=list)
    extract_seconds: float | None = None
    warnings: list[str] = field(default_factory=list)
    unavailable_reason: str | None = None

    @property
    def available(self) -> bool:
        return self.unavailable_reason is None and self.frames_processed > 0


def _iou(a, b) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(1e-9, (ax1 - ax0) * (ay1 - ay0))
    area_b = max(1e-9, (bx1 - bx0) * (by1 - by0))
    return inter / (area_a + area_b - inter)


def _clean(text: str) -> str:
    return _WS.sub(" ", (text or "").strip())


def _is_noise(text: str) -> bool:
    t = _clean(text).lower()
    if len(t) < MIN_CHARS:
        return True
    if t in NOISE_TOKENS:
        return True
    # A detection with no letters at all is a stave line or a key edge far
    # more often than it is a word.
    if not re.search(r"[a-zA-ZÀ-ɏ]", t):
        return True
    return False


def analyse_frames(frames: np.ndarray, *, timestamps: list[float],
                   hook_seconds: float = 3.0) -> OcrResult:
    """Run OCR over already-decoded frames.

    Takes frames rather than a path so the caller decodes once and shares
    the result with the deterministic pass.
    """
    import time
    started = time.monotonic()
    out = OcrResult(hook_seconds=hook_seconds)

    if frames is None or frames.shape[0] == 0:
        out.unavailable_reason = "no frames were decoded from the window"
        out.extract_seconds = 0.0
        return out

    try:
        engine = _load()
    except Exception as exc:                       # noqa: BLE001
        out.unavailable_reason = (
            f"the OCR engine could not be loaded ({type(exc).__name__}: "
            f"{exc}). No text claim is made either way.")
        out.extract_seconds = round(time.monotonic() - started, 3)
        return out

    h, w = frames.shape[1], frames.shape[2]
    for frame, at in zip(frames, timestamps):
        try:
            result, _elapsed = engine(frame)
        except Exception as exc:                   # noqa: BLE001
            out.frames_failed += 1
            out.warnings.append(f"frame at {at:.2f}s failed: {exc}")
            continue
        out.frames_processed += 1
        for item in (result or []):
            box_pts, text, score = item[0], item[1], float(item[2])
            xs = [float(p[0]) for p in box_pts]
            ys = [float(p[1]) for p in box_pts]
            box = (min(xs) / w, min(ys) / h, max(xs) / w, max(ys) / h)
            out.detections.append(Detection(
                text=_clean(text), score=round(score, 3), box=box,
                at_s=round(float(at), 2),
                height_frac=round(max(1e-6, box[3] - box[1]), 4)))

    out.elements = _track(out.detections)
    out.extract_seconds = round(time.monotonic() - started, 3)
    return out


def _track(detections: list[Detection]) -> list[TextElement]:
    """Group per-frame detections into elements held across frames."""
    elements: list[TextElement] = []
    buckets: list[list[Detection]] = []
    for det in sorted(detections, key=lambda d: d.at_s):
        if _is_noise(det.text) or det.score < MIN_SCORE:
            continue
        placed = False
        for bucket in buckets:
            if _iou(bucket[0].box, det.box) >= IOU_SAME:
                bucket.append(det)
                placed = True
                break
        if not placed:
            buckets.append([det])

    for bucket in buckets:
        texts = [d.text for d in bucket]
        # The most frequent spelling wins; the others are kept as variants
        # because disagreement across frames is an uncertainty signal.
        best = max(set(texts), key=texts.count)
        elements.append(TextElement(
            text=best,
            first_seen_s=min(d.at_s for d in bucket),
            last_seen_s=max(d.at_s for d in bucket),
            frames_seen=len(bucket),
            mean_score=round(sum(d.score for d in bucket) / len(bucket), 3),
            box=bucket[0].box,
            height_frac=round(
                sum(d.height_frac for d in bucket) / len(bucket), 4),
            variants=sorted(set(texts))))
    # Reading order: top to bottom, then left to right. Sorting by
    # first-appearance instead produced "in my next album? Shall I include
    # this" for a two-line caption - every word right, the sentence wrong.
    # Lines within ~4% of frame height are treated as the same line.
    def reading_key(e: TextElement):
        return (round(e.box[1] / 0.04), e.box[0])

    return sorted(elements, key=reading_key)


def measurements(result: OcrResult, interval: tuple[float, float]
                 ) -> list[dict]:
    """Shape an `OcrResult` like `hookfeat.Measurement` dicts."""
    iv = list(interval)
    params = {"engine": result.engine, "ocr_fps": OCR_FPS,
              "ocr_width": OCR_WIDTH, "min_score": MIN_SCORE,
              "frames_processed": result.frames_processed,
              "frames_failed": result.frames_failed}
    note = (f"{result.engine} over {result.frames_processed} sampled "
            f"frame(s). OCR is not ground truth: it mis-reads stylised "
            f"fonts and finds text in sheet music and keyboard edges. Each "
            f"value carries the engine's own score. It also does NOT "
            f"distinguish a caption the creator added from printed text "
            f"already in the scene - sheet music markings are read as text "
            f"like any other, and separating the two needs the vision "
            f"model, not the OCR engine.")

    if not result.available:
        reason = (result.unavailable_reason
                  or "no frames could be processed")
        return [{"name": n, "value": None, "method": "unavailable",
                 "interval": iv, "unavailable_reason": reason,
                 "params": params}
                for n in ("ocr_text", "ocr_text_present",
                          "ocr_element_count", "ocr_first_text_at_s",
                          "ocr_text_duration_s", "ocr_max_text_height_frac",
                          "ocr_mean_confidence", "ocr_text_changes",
                          "ocr_word_count")]

    els = result.elements
    joined = " | ".join(e.text for e in els)
    heights = [e.height_frac for e in els]
    scores = [e.mean_score for e in els]
    first = min((e.first_seen_s for e in els), default=None)
    # Union of on-screen time, not the sum: two captions shown together are
    # not six seconds of text in a three-second window.
    spans = sorted((e.first_seen_s, e.last_seen_s) for e in els)
    union = 0.0
    cur_a = cur_b = None
    for a, b in spans:
        if cur_a is None:
            cur_a, cur_b = a, b
        elif a <= cur_b:
            cur_b = max(cur_b, b)
        else:
            union += cur_b - cur_a
            cur_a, cur_b = a, b
    if cur_a is not None:
        union += cur_b - cur_a
    # Elements that start after the first one: the hook's text changed.
    changes = sum(1 for e in els
                  if first is not None and e.first_seen_s > first + 0.01)

    return [
        {"name": "ocr_text", "value": joined or None,
         "method": "model", "interval": iv, "params": params, "note": note,
         "unavailable_reason": (None if joined else
                                "the engine found no text it would stand "
                                "behind in this window"),
         "uncertainty": (round(sum(scores) / len(scores), 3)
                         if scores else None),
         "uncertainty_kind": "confidence"},
        {"name": "ocr_text_present", "value": bool(els), "method": "model",
         "interval": iv, "params": params, "note": note,
         "uncertainty": (round(min(scores), 3) if scores else None),
         "uncertainty_kind": "confidence"},
        {"name": "ocr_element_count", "value": len(els), "method": "model",
         "unit": "elements", "interval": iv, "params": params},
        {"name": "ocr_word_count",
         "value": sum(len(e.text.split()) for e in els), "method": "model",
         "unit": "words", "interval": iv, "params": params},
        {"name": "ocr_text_reading_order", "value": True,
         "method": "model", "interval": iv, "params": params,
         "note": "`ocr_text` joins elements top-to-bottom then "
                 "left-to-right, not in the order they appeared."},
        {"name": "ocr_first_text_at_s", "value": first, "method": "model",
         "unit": "s", "interval": iv,
         "uncertainty": round(1.0 / OCR_FPS, 3),
         "uncertainty_kind": "resolution", "params": params,
         "unavailable_reason": (None if first is not None
                                else "no text was found")},
        {"name": "ocr_text_duration_s", "value": round(union, 2),
         "method": "model", "unit": "s", "interval": iv,
         "uncertainty": round(1.0 / OCR_FPS, 3),
         "uncertainty_kind": "resolution", "params": params,
         "note": "Union of on-screen time, not the sum: two captions shown "
                 "together are not six seconds of text in three."},
        {"name": "ocr_max_text_height_frac",
         "value": round(max(heights), 4) if heights else None,
         "method": "model", "unit": "fraction of frame height",
         "interval": iv, "params": params,
         "note": "Relative, because this corpus mixes 360x640 and "
                 "720x1280 sources and absolute pixels are not comparable."},
        {"name": "ocr_mean_confidence",
         "value": round(sum(scores) / len(scores), 3) if scores else None,
         "method": "model", "unit": "0-1", "interval": iv, "params": params,
         "note": "The engine's own score. It is not calibrated and a high "
                 "score on a mis-read is common."},
        {"name": "ocr_text_changes", "value": changes, "method": "model",
         "unit": "elements appearing later", "interval": iv,
         "params": params,
         "note": "Text elements that appear after the first one, i.e. the "
                 "caption changed during the hook."},
        {"name": "ocr_elements", "method": "model", "interval": iv,
         "params": params,
         "value": [{"text": e.text, "first_seen_s": e.first_seen_s,
                    "last_seen_s": e.last_seen_s,
                    "duration_s": e.duration_s,
                    "frames_seen": e.frames_seen,
                    "score": e.mean_score, "box": list(e.box),
                    "height_frac": e.height_frac,
                    "variants": e.variants} for e in els]},
    ]
