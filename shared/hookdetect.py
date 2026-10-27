"""Piano keyboards and sheet music from pixels — MEASURED, AND IT FAILS.

> **Result, 2026-10-27. This approach does not work and is not wired
> into anything.** Scored against the 24 human reference labels:
>
> | field | recall | specificity | lift | note |
> |---|---|---|---|---|
> | keyboard | **0.000** | 1.000 | −0.75 | never fired on any of the 21 positives |
> | sheet music | **0.000** | 0.647 | −0.25 | 6 false positives, 0 true positives |
>
> The specificity of 1.000 on keyboards is not a success: it is what
> "always no" scores. Raising the analysis width from 160 px to 480 px
> made it worse — agreeing-row counts peaked at 5 for positives against
> 2 for negatives, which is no separation.
>
> The module and its tests are kept as the record of an approach that
> was tried and measured, not as a component. `server/features/extract.
> run_detect` was written and then removed the same day, because a
> pipeline stage producing a field nothing may use is worse than no
> stage.
>
> **Why it fails, as far as the diagnostic shows:** real keyboards are
> photographed at an angle, half-occluded by hands, unevenly lit and
> often small in frame. Row-wise autocorrelation assumes a
> front-parallel, unobstructed, evenly lit grating. The synthetic tests
> below pass because synthetic keyboards are exactly that, which is
> precisely why passing them proved nothing.

Original design notes follow.


M3. The Gemma 3 4B benchmark (docs/benchmarks/6c-vision-benchmark.md)
measured `piano_or_keyboard_visible` at specificity **0.000** — it
reported a piano in all three openings that had none, including a man
walking through a garden — and `sheet_music_visible` at 0.235, finding
sheet music in 13 openings without any. Both are withheld from analysis
because of it.

Both objects are, however, among the most geometrically regular things a
camera can see:

* a **keyboard** is a near-periodic black-and-white pattern along one
  direction, with a characteristic duty cycle — wide white keys broken by
  narrow black ones;
* a **staff** is five parallel lines at near-equal spacing, repeated down
  the page.

Neither needs semantics. Both are detectable by looking for the specific
periodicity they create, which is what this module does. A 4B language
model guessing from a caption-shaped prior is the wrong tool; an
autocorrelation is the right one.

## What is claimed, and what is not

This module produces a **score and a decision per frame set**, with the
evidence that produced it. It does not claim to be right. M3's promotion
rule applies to it exactly as it applies to a model: a field enters
analytical use only at specificity ≥ 0.80 and lift ≥ +0.10 on the
held-out set, measured, never assumed. Until that measurement exists
these outputs are stored and withheld like any other unproven field.

The known failure modes, stated in advance so the benchmark can look for
them: piano detection will fire on zebra crossings, barcodes, striped
clothing, venetian blinds and railings, all of which are periodic
light-dark patterns. Staff detection will fire on lined paper, radiators,
shutters and fences. Both are deliberately biased toward **precision over
recall** — a missed keyboard costs a true negative, a hallucinated one
repeats the exact failure the vision model was disqualified for.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger("virallab.hookdetect")

METHOD_VERSION = "hook-detect-v1"

#: A keyboard occupies a band, not the whole frame, so rows are scanned
#: individually and the best run of agreeing rows is what counts. One
#: lucky row is a coincidence; forty consecutive rows agreeing is a
#: keyboard.
MIN_KEY_ROWS = 12

#: White keys in a frontal shot of a piano span roughly 1.5–6% of frame
#: width depending on how much of the instrument is in shot. Expressed as
#: a period in pixels at the analysis width, with generous margins.
KEY_PERIOD_MIN = 6
KEY_PERIOD_MAX = 60

#: Autocorrelation peak a row must reach to count as periodic. High on
#: purpose: see the precision-over-recall note in the module docstring.
KEY_PERIODICITY = 0.45

#: A staff is five lines; detecting four is enough to be interesting and
#: avoids failing on a line clipped by the frame edge.
MIN_STAFF_LINES = 4
#: Staff line spacing, in pixels at the analysis width.
STAFF_SPACING_MIN = 3
STAFF_SPACING_MAX = 40
#: How equal the gaps between candidate lines must be. A staff is ruled;
#: a radiator is not quite.
STAFF_SPACING_TOLERANCE = 0.22


@dataclass
class Detection:
    """One object decision, with the numbers behind it."""

    name: str
    present: bool
    score: float
    #: Everything a human needs to audit the decision without re-running
    #: it. A bare boolean from an unproven detector is not evidence.
    evidence: dict = field(default_factory=dict)
    method_version: str = METHOD_VERSION


def _to_gray(frames: np.ndarray) -> np.ndarray:
    """(N, H, W, 3) uint8 to (N, H, W) float32 in 0..1."""
    if frames.ndim == 3:
        frames = frames[None, ...]
    g = (0.299 * frames[..., 0] + 0.587 * frames[..., 1]
         + 0.114 * frames[..., 2])
    return (g / 255.0).astype(np.float32)


#: A periodic pattern must ANTI-correlate at half its period: half a
#: cycle out of phase, white keys land on black ones. A monotone ramp
#: does not — it correlates positively at every short lag, which is how
#: a smooth brightness gradient passed the first version of this
#: detector with a peak of 0.94. Requiring the half-period trough is
#: what distinguishes "oscillating" from "sloping".
KEY_ANTIPHASE_MAX = -0.15


def _row_periodicity(row: np.ndarray) -> tuple[float, int]:
    """Strongest OSCILLATORY autocorrelation peak in the key-period band.

    Returns (peak, period), or (0, 0) when nothing in the band both
    correlates at a lag and anti-correlates at half of it. The row is
    mean-removed first so overall brightness cannot masquerade as
    structure — necessary but, as the gradient case showed, not
    sufficient.
    """
    x = row - row.mean()
    denom = float((x * x).sum())
    if denom <= 1e-6:
        return 0.0, 0
    n = x.size
    hi = min(KEY_PERIOD_MAX, n // 2)
    if hi <= KEY_PERIOD_MIN:
        return 0.0, 0

    def corr(lag: int) -> float:
        if lag <= 0 or lag >= n:
            return 0.0
        return float((x[:-lag] * x[lag:]).sum()) / denom

    best, best_lag = 0.0, 0
    for lag in range(KEY_PERIOD_MIN, hi + 1):
        c = corr(lag)
        if c <= best:
            continue
        if corr(lag // 2) > KEY_ANTIPHASE_MAX:
            continue          # sloping, not oscillating
        best, best_lag = c, lag
    return best, best_lag


def detect_keyboard(frames: np.ndarray) -> Detection:
    """Is there a piano keyboard in these frames?

    A keyboard makes every row that crosses it periodic at the same
    spatial frequency, with a high-contrast bimodal brightness profile.
    Requiring agreement across many adjacent rows is what separates a
    keyboard from a single striped object.
    """
    gray = _to_gray(frames)
    best_frame = {"rows": 0, "period": 0, "peak": 0.0, "bimodality": 0.0}

    for f in range(gray.shape[0]):
        img = gray[f]
        periods: list[int] = []
        peaks: list[float] = []
        for r in range(img.shape[0]):
            peak, period = _row_periodicity(img[r])
            if peak >= KEY_PERIODICITY and period:
                periods.append(period)
                peaks.append(peak)
            else:
                periods.append(0)
                peaks.append(0.0)

        # Longest run of consecutive rows agreeing on a period, within a
        # pixel or two: a keyboard is a band, not scattered rows.
        run = best_run = 0
        run_period = best_period = 0
        run_peaks: list[float] = []
        best_peaks: list[float] = []
        for r, p in enumerate(periods):
            if p and (run_period == 0 or abs(p - run_period) <= 2):
                run += 1
                run_period = run_period or p
                run_peaks.append(peaks[r])
            else:
                if run > best_run:
                    best_run, best_period = run, run_period
                    best_peaks = list(run_peaks)
                run = 1 if p else 0
                run_period = p
                run_peaks = [peaks[r]] if p else []
        if run > best_run:
            best_run, best_period = run, run_period
            best_peaks = list(run_peaks)

        if best_run <= best_frame["rows"]:
            continue

        # Keys are black OR white, not a smooth gradient: the band's
        # brightness histogram should be bimodal and wide.
        band = img[max(0, 0):, :]
        lo, hi = np.percentile(band, 10), np.percentile(band, 90)
        best_frame = {
            "rows": int(best_run),
            "period": int(best_period),
            "peak": float(np.mean(best_peaks)) if best_peaks else 0.0,
            "bimodality": float(hi - lo),
        }

    present = (best_frame["rows"] >= MIN_KEY_ROWS
               and best_frame["bimodality"] >= 0.35)
    # Score is the evidence strength, not a probability. Naming it a
    # probability would invite exactly the over-reading this project
    # spends its time avoiding.
    score = min(1.0, (best_frame["rows"] / max(1, MIN_KEY_ROWS * 3))
                * best_frame["peak"] * 2.0)
    return Detection(
        name="keyboard_visible", present=present, score=round(score, 4),
        evidence={**best_frame,
                  "min_rows": MIN_KEY_ROWS,
                  "periodicity_threshold": KEY_PERIODICITY,
                  "known_false_positives": ["zebra crossing", "barcode",
                                            "striped clothing", "blinds",
                                            "railings"]})


def detect_staff(frames: np.ndarray) -> Detection:
    """Is there printed sheet music in these frames?

    A staff is five evenly spaced horizontal dark lines. The test is
    therefore on the *regularity of spacing* between dark row-runs, not
    on darkness itself — a shadow is dark, a staff is ruled.
    """
    gray = _to_gray(frames)
    best = {"lines": 0, "spacing": 0.0, "evenness": 0.0}

    for f in range(gray.shape[0]):
        img = gray[f]
        # Mean brightness per row; a staff line is a local minimum.
        prof = img.mean(axis=1)
        if prof.size < 10:
            continue
        thresh = prof.mean() - 0.6 * prof.std()
        dark = prof < thresh
        centres: list[int] = []
        r = 0
        while r < dark.size:
            if dark[r]:
                start = r
                while r < dark.size and dark[r]:
                    r += 1
                # A staff line is thin. A dark band is a shadow.
                if (r - start) <= max(3, img.shape[0] // 60):
                    centres.append((start + r) // 2)
            else:
                r += 1
        if len(centres) < MIN_STAFF_LINES:
            continue
        gaps = np.diff(np.array(centres, dtype=np.float32))
        in_band = [g for g in gaps
                   if STAFF_SPACING_MIN <= g <= STAFF_SPACING_MAX]
        if len(in_band) < MIN_STAFF_LINES - 1:
            continue
        arr = np.array(in_band, dtype=np.float32)
        evenness = float(arr.std() / max(1e-6, arr.mean()))
        if evenness > STAFF_SPACING_TOLERANCE:
            continue
        if len(in_band) + 1 > best["lines"]:
            best = {"lines": int(len(in_band) + 1),
                    "spacing": float(arr.mean()),
                    "evenness": round(evenness, 4)}

    present = best["lines"] >= MIN_STAFF_LINES
    score = min(1.0, best["lines"] / 5.0) if present else 0.0
    return Detection(
        name="sheet_music_visible", present=present, score=round(score, 4),
        evidence={**best,
                  "min_lines": MIN_STAFF_LINES,
                  "spacing_tolerance": STAFF_SPACING_TOLERANCE,
                  "known_false_positives": ["lined paper", "radiator",
                                            "shutters", "fence"]})


def detect_all(frames: np.ndarray) -> list[Detection]:
    return [detect_keyboard(frames), detect_staff(frames)]


def measurements(detections: list[Detection],
                 interval: tuple[float, float]) -> list[dict]:
    """Shape detections like `hookfeat.Measurement` dicts.

    `method` is `deterministic`, not `model`, because that is what these
    are — and the distinction decides whether a reader treats the output
    as a measurement or as an opinion.
    """
    iv = list(interval)
    out: list[dict] = []
    for d in detections:
        out.append({
            "name": f"detect_{d.name}",
            "value": bool(d.present),
            "method": "deterministic",
            "interval": iv,
            "uncertainty": d.score,
            "uncertainty_kind": "evidence_strength",
            "params": d.evidence,
            "note": ("Geometric detector, NOT a model. Unproven until "
                     "scored on M3's held-out set at specificity >= 0.80 "
                     "and lift >= +0.10; withheld from analysis until "
                     "then, exactly like a model-derived field."),
        })
        out.append({
            "name": f"detect_{d.name}_score",
            "value": d.score,
            "method": "deterministic",
            "interval": iv,
            "params": {"method_version": d.method_version},
        })
    return out
