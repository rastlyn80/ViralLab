"""Regression tests for the spectral audio fingerprint.

These run on **both** machines: `shared/audiofp.py` is deployed to the
server, which fingerprints Instagram media in place rather than shipping
736 MB of video to Windows.

Each test pins a property that was measured, or a defect that was found and
fixed. The numbers come from docs/benchmarks/2-audio-fingerprint.md.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from shared import audiofp


# --------------------------------------------------------------------------
# synthetic audio, so the tests need no media files and no ffmpeg
# --------------------------------------------------------------------------

def tone(seconds: float, freqs=(220.0, 440.0, 880.0), *,
         rate: int = audiofp.SAMPLE_RATE, seed: int = 0,
         gain: float = 0.5) -> np.ndarray:
    """A signal with structure: drifting partials plus noise.

    Band-energy *changes* are what the fingerprint encodes, so a constant
    tone would produce no information at all. The amplitude modulation is
    what gives each band something to change about.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * rate)) / rate
    sig = np.zeros_like(t)
    for k, f in enumerate(freqs):
        env = 0.5 + 0.5 * np.sin(2 * math.pi * (0.7 + 0.31 * k) * t + k)
        sig += env * np.sin(2 * math.pi * f * t)
    sig += 0.05 * rng.standard_normal(t.size)
    return (gain * sig / max(1e-9, np.abs(sig).max())).astype(np.float32)


def fp_from(samples: np.ndarray) -> dict:
    """Build a fingerprint dict from samples, mirroring fingerprint()."""
    phases = []
    for p in range(audiofp.PHASES):
        shifted = samples[p * (audiofp.HOP // audiofp.PHASES):]
        words = audiofp.fingerprint_samples(shifted)
        if len(words) >= audiofp.MIN_ALIGNED_FRAMES:
            phases.append([f"{w:04x}" for w in words])
    assert phases, "test signal too short to fingerprint"
    return {"v": 3, "rate": audiofp.SAMPLE_RATE, "hop": audiofp.HOP,
            "phases": len(phases), "words": phases[0], "alt": phases[1:],
            "seconds": round(len(samples) / audiofp.SAMPLE_RATE, 2)}


# --------------------------------------------------------------------------
# the defect that made the first benchmark read as "not separable"
# --------------------------------------------------------------------------

def test_sub_frame_offset_still_matches():
    """A trim that is not a whole number of hops must still match.

    This is the bug that made `offset_1s` score **0.448** while correctly
    reporting a -1.02 s offset: every FFT window straddled different audio.
    Storing the fingerprint at two sub-frame phases fixed it, taking the
    same case to 0.999.
    """
    base = tone(14.0, seed=1)
    shift = int(0.5 * audiofp.HOP) + 7          # deliberately not a hop
    a, b = fp_from(base), fp_from(base[shift:])
    r = audiofp.compare(a, b)
    assert r["comparable"]
    assert r["score"] >= 0.85, (
        f"a sub-frame shift scored only {r['score']:.3f}; the phase "
        f"fix has regressed")


def test_phases_are_actually_stored():
    fp = fp_from(tone(10.0, seed=2))
    assert fp["phases"] == audiofp.PHASES
    assert len(fp["alt"]) == audiofp.PHASES - 1
    assert len(audiofp._all_phases(fp)) == audiofp.PHASES


# --------------------------------------------------------------------------
# the defect that merged 33 unrelated performances into one group
# --------------------------------------------------------------------------

def test_tiny_aligned_span_cannot_score_high():
    """A short agreeable window must not produce a confident match.

    With the old 12-frame floor, `Billie Eilish.mp4` and `Unicef.mp4` - two
    unrelated files of 517 and 560 frames - scored **1.000 on 22 aligned
    frames**, 4% of either. Chained through grouping, links like that merged
    33 unrelated performances into a single group.

    Here: two unrelated signals that share a brief identical passage.
    """
    shared = tone(1.0, seed=3)
    a = np.concatenate([tone(9.0, seed=10), shared, tone(9.0, seed=11)])
    b = np.concatenate([tone(9.0, seed=20), shared, tone(9.0, seed=21)])
    r = audiofp.compare(fp_from(a), fp_from(b))
    assert r["score"] < audiofp.MIN_COVERAGE + 0.2, (
        f"a 1 s shared passage inside two unrelated signals scored "
        f"{r['score']:.3f} over {r['aligned_frames']} frames")


def test_coverage_is_reported_and_bounded():
    fp = fp_from(tone(12.0, seed=4))
    r = audiofp.compare(fp, fp)
    assert r["coverage"] == pytest.approx(1.0, abs=0.01)
    assert 0.0 <= r["coverage"] <= 1.0


def test_min_aligned_floor_is_not_the_old_value():
    """Pins the fix, so lowering the floor again fails loudly."""
    assert audiofp.MIN_ALIGNED_FRAMES >= 43, (
        "the 12-frame floor allowed confident matches on half a second of "
        "audio")
    assert audiofp.MIN_COVERAGE >= 0.5


# --------------------------------------------------------------------------
# properties the matcher relies on
# --------------------------------------------------------------------------

def test_identical_audio_scores_one():
    fp = fp_from(tone(12.0, seed=5))
    r = audiofp.compare(fp, fp)
    assert r["score"] == pytest.approx(1.0)
    assert r["offset_s"] == pytest.approx(0.0, abs=0.1)


def test_unrelated_audio_scores_well_below_the_floor():
    a, b = fp_from(tone(12.0, seed=6)), fp_from(tone(12.0, seed=7))
    r = audiofp.compare(a, b)
    assert r["score"] < 0.70, (
        f"unrelated signals scored {r['score']:.3f}, at or above the "
        f"matching floor")


def test_shortened_clip_matches_its_source():
    """Half-length clip vs full master - the envelope scored 0.583-0.691 on
    this, *below* what it scored on true negatives. The spectral method
    scored 1.000."""
    base = tone(20.0, seed=8)
    r = audiofp.compare(fp_from(base), fp_from(base[:len(base) // 2]))
    assert r["score"] >= 0.85
    assert r["coverage"] >= audiofp.MIN_COVERAGE


def test_offset_is_reported_with_the_right_sign_and_size():
    """A nonzero offset is itself evidence: it means a trimmed head or a
    different opening hook, which is exactly what the owner changes between
    platforms."""
    base = tone(18.0, seed=9)
    cut_s = 3.0
    trimmed = base[int(cut_s * audiofp.SAMPLE_RATE):]
    r = audiofp.compare(fp_from(base), fp_from(trimmed))
    assert r["score"] >= 0.85
    assert abs(abs(r["offset_s"]) - cut_s) < 0.25, (
        f"reported offset {r['offset_s']} does not match the {cut_s}s trim")


def test_loudness_change_does_not_affect_the_score():
    """The fingerprint encodes the SIGN of band-energy change, so a gain
    change cancels. The envelope degraded to 0.739-0.877 here."""
    base = tone(12.0, seed=12, gain=0.5)
    quiet = (base * 0.15).astype(np.float32)
    r = audiofp.compare(fp_from(base), fp_from(quiet))
    assert r["score"] >= 0.95, f"gain change cost {1 - r['score']:.3f}"


# --------------------------------------------------------------------------
# safety: never claim comparability that does not exist
# --------------------------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    (None, None),
    (None, {"words": ["0001"] * 60}),
    ({}, {"words": ["0001"] * 60}),
    ({"words": []}, {"words": ["0001"] * 60}),
])
def test_missing_fingerprints_are_not_comparable(a, b):
    r = audiofp.compare(a, b)
    assert r["comparable"] is False
    assert r["score"] == 0.0


def test_too_short_to_judge_is_not_a_match():
    """A clip shorter than the alignment floor must not match anything -
    'unverifiable' and 'verified different' are not the same thing."""
    short = fp_from(tone(3.0, seed=13))
    long_ = fp_from(tone(15.0, seed=13))
    r = audiofp.compare(short, long_)
    # Either not comparable, or scored on a span that meets the floor.
    assert (not r["comparable"]) or r["aligned_frames"] >= \
        audiofp.MIN_ALIGNED_FRAMES


def test_quick_mode_is_cheaper_and_never_more_confident_than_full():
    """The screen decides what to look at properly, not what matches, so it
    must not be the thing that produces a high score on its own."""
    base = tone(60.0, seed=14)
    a, b = fp_from(base), fp_from(base)
    q = audiofp.compare(a, b, quick=True)
    f = audiofp.compare(a, b)
    assert q["comparable"] and f["comparable"]
    assert q["aligned_frames"] <= f["aligned_frames"]


def test_fingerprint_is_deterministic():
    samples = tone(10.0, seed=15)
    assert (audiofp.fingerprint_samples(samples)
            == audiofp.fingerprint_samples(samples))
