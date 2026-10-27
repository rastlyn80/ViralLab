"""M3: geometric detectors for the objects the vision model could not see.

The Gemma 3 4B benchmark measured `piano_or_keyboard_visible` at
specificity **0.000** — it reported a piano in every opening that had
none — and `sheet_music_visible` at 0.235. These detectors exist because
both objects are geometrically regular and need no semantics.

The tests that matter here are the **negative** ones. A detector that
fires on everything has perfect recall and is worthless, which is
precisely how the model failed. So the striped-object cases below are
not edge cases; they are the point.
"""

from __future__ import annotations

import numpy as np
import pytest

from shared import hookdetect as hd

H, W = 180, 320
RNG = np.random.default_rng(20261027)


def _blank(value: int = 128) -> np.ndarray:
    return np.full((H, W, 3), value, np.uint8)


def _noisy(value: int = 128, amp: int = 5) -> np.ndarray:
    base = _blank(value).astype(np.int16)
    base += RNG.integers(-amp, amp + 1, (H, W, 3))
    return np.clip(base, 0, 255).astype(np.uint8)


def _keyboard(period: int = 14, top: int = 100, bottom: int = 170
              ) -> np.ndarray:
    img = _blank(30)
    img[top:bottom, :, :] = 245
    for x in range(0, W, period):
        img[top:top + (bottom - top) * 2 // 3, x:x + 4, :] = 20
    return img


def _staff(spacing: int = 8, lines: int = 5) -> np.ndarray:
    img = _blank(240)
    for i in range(lines):
        y = 40 + i * spacing
        img[y:y + 2, :, :] = 25
    return img


# ---------------------------------------------------------------------------
# it finds what it should
# ---------------------------------------------------------------------------

def test_a_keyboard_is_found():
    d = hd.detect_keyboard(_keyboard())
    assert d.present
    assert d.evidence["rows"] >= hd.MIN_KEY_ROWS
    assert hd.KEY_PERIOD_MIN <= d.evidence["period"] <= hd.KEY_PERIOD_MAX


def test_a_staff_is_found_and_its_spacing_reported():
    d = hd.detect_staff(_staff(spacing=8))
    assert d.present
    assert d.evidence["lines"] >= hd.MIN_STAFF_LINES
    assert d.evidence["spacing"] == pytest.approx(8.0, abs=1.0)


def test_four_lines_are_enough_so_a_clipped_staff_still_counts():
    """A staff whose top line is cut by the frame edge is still a staff."""
    assert hd.detect_staff(_staff(lines=4)).present


# ---------------------------------------------------------------------------
# it stays quiet when it should — the half the model failed
# ---------------------------------------------------------------------------

def test_a_plain_wall_is_not_a_piano():
    """The literal failure case: the model reported a piano in a garden."""
    d = hd.detect_keyboard(_noisy())
    assert not d.present
    assert d.score == 0.0


def test_a_plain_wall_is_not_sheet_music():
    assert not hd.detect_staff(_noisy()).present


def test_a_smooth_gradient_is_not_a_keyboard():
    """Brightness variation is not structure. Mean-removal should make a
    gradient invisible to the periodicity test."""
    grad = np.tile(np.linspace(20, 240, W, dtype=np.uint8), (H, 1))
    img = np.dstack([grad] * 3)
    assert not hd.detect_keyboard(img).present


def test_a_dark_band_is_not_a_staff():
    """A shadow is dark and wide; a staff line is dark and thin."""
    img = _blank(240)
    img[60:110, :, :] = 30
    assert not hd.detect_staff(img).present


def test_a_single_striped_strip_is_not_a_keyboard():
    """One narrow band of stripes is a barcode, a railing or a sleeve.

    A keyboard makes MANY adjacent rows periodic at one frequency, which
    is the whole reason `MIN_KEY_ROWS` exists.
    """
    img = _noisy(200)
    img[90:95, :, :] = 240
    for x in range(0, W, 10):
        img[90:95, x:x + 3, :] = 15
    d = hd.detect_keyboard(img)
    assert not d.present, (
        f"fired on a {5}-row strip; rows={d.evidence['rows']}")


def test_the_detector_reports_its_own_known_false_positives():
    """Both detectors carry the list of things they are known to confuse.

    Stated in the output, not only in a docstring, so a reader auditing a
    stored row sees the caveat without finding the source.
    """
    for d in hd.detect_all(_keyboard()):
        assert d.evidence["known_false_positives"]


# ---------------------------------------------------------------------------
# how it is stored
# ---------------------------------------------------------------------------

def test_measurements_are_marked_deterministic_and_withheld():
    """These are measurements, not a model's opinion — and they are still
    unproven until M3's held-out set scores them."""
    ms = hd.measurements(hd.detect_all(_keyboard()), (0.0, 3.0))
    assert ms
    for m in ms:
        assert m["method"] == "deterministic"
    notes = [m.get("note", "") for m in ms]
    assert any("withheld from analysis" in n for n in notes)
    assert any("specificity >= 0.80" in n for n in notes)


def test_a_detection_carries_the_evidence_that_produced_it():
    d = hd.detect_keyboard(_keyboard())
    for key in ("rows", "period", "peak", "bimodality"):
        assert key in d.evidence
    assert d.method_version == hd.METHOD_VERSION
