"""Regression tests for local-master to post matching.

The headline case is `test_identical_visuals_different_audio_is_not_a_match`:
the owner's "musical portraits" series are visually identical (same framing,
same piano, ~9 s each) and differ only in the music. Frame hashing alone
scored them 1.00 against each other and produced ~10 confident FALSE
auto-links. Audio is what separates them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "probes" / "local_library"))
sys.path.insert(0, str(ROOT / "scripts"))

from fingerprint import (  # noqa: E402
    best_overlap, dhash_frame, envelope_similarity, hamming,
)
from match_local_to_posts import AUDIO_MATCH_FLOOR, AUTO_LINK, score  # noqa: E402


def fp(hashes: list[int], duration: float, audio: list[int] | None = None):
    return {"hashes": [f"{h:016x}" for h in hashes], "duration_s": duration,
            "audio": audio, "frames": len(hashes)}


LOUD = [10, 200, 50, 180, 30, 160, 90, 20]
QUIET = [200, 10, 190, 20, 170, 40, 15, 210]


# ---------------------------------------------------------------------------
# the false-positive class that motivated audio fingerprinting
# ---------------------------------------------------------------------------

def test_identical_visuals_different_audio_is_not_a_match():
    """Two posts in a visually identical series must NOT auto-link to each
    other's master. This is the exact failure found on real data."""
    frames = [0x1122334455667788] * 12
    master = fp(frames, 9.1, LOUD)
    other_post = fp(frames, 9.13, QUIET)        # same look, different music
    s, detail = score(master, other_post)
    assert detail["frame_overlap"] == 1.0, "visuals should look identical"
    assert detail["audio_similarity"] < AUDIO_MATCH_FLOOR
    assert s < AUTO_LINK, (
        f"scored {s:.2f}: a visually identical sibling would auto-link again")


def test_same_visuals_and_same_audio_does_match():
    frames = [0x1122334455667788] * 12
    master = fp(frames, 9.1, LOUD)
    its_post = fp(frames, 9.12, LOUD)
    s, detail = score(master, its_post)
    assert detail["audio_similarity"] >= AUDIO_MATCH_FLOOR
    assert s >= AUTO_LINK, f"a true match scored only {s:.2f}"


def test_missing_audio_is_capped_below_auto_link():
    """Unverifiable is not the same as verified. Without audio on both sides
    the pair is reviewed rather than assumed."""
    frames = [0x1122334455667788] * 12
    s, detail = score(fp(frames, 9.1, None), fp(frames, 9.1, LOUD))
    assert detail["has_audio"] is False
    assert s < AUTO_LINK


# ---------------------------------------------------------------------------
# partial overlap: edited variants and reposts
# ---------------------------------------------------------------------------

def test_partial_overlap_scores_on_the_shared_stretch():
    """An edit using only part of a master must still be recognised - this is
    what supports reposts and variants."""
    master = [0x1111111111111111, 0x2222222222222222, 0x3333333333333333,
              0x4444444444444444, 0x5555555555555555, 0x6666666666666666]
    excerpt = master[2:5]
    overlap, offset = best_overlap(master, excerpt)
    assert overlap == 1.0
    assert offset == 2, f"aligned at {offset}, expected the true offset 2"


def test_unrelated_sequences_do_not_overlap():
    a = [0x0000000000000000] * 6
    b = [0xFFFFFFFFFFFFFFFF] * 6
    overlap, _ = best_overlap(a, b)
    assert overlap == 0.0


def test_overlap_tolerates_small_per_frame_noise():
    """Re-encoding flips a few bits; the match must survive that."""
    base = [0x1122334455667788] * 5
    noisy = [h ^ 0b1010 for h in base]          # 2 bits per frame
    assert all(hamming(a, b) <= 12 for a, b in zip(base, noisy))
    overlap, _ = best_overlap(base, noisy)
    assert overlap == 1.0


# ---------------------------------------------------------------------------
# primitives
# ---------------------------------------------------------------------------

def test_envelope_similarity_bounds():
    assert envelope_similarity(LOUD, LOUD) == 1.0
    assert envelope_similarity(LOUD, QUIET) < 0.8
    assert envelope_similarity(None, LOUD) == 0.0
    assert envelope_similarity(LOUD, LOUD[:4]) == 0.0      # length mismatch


def test_dhash_distinguishes_different_images():
    pytest.importorskip("PIL")
    from PIL import Image
    left = Image.new("L", (9, 8), 0)
    for y in range(8):
        for x in range(9):
            left.putpixel((x, y), 0 if x < 4 else 255)
    right = Image.new("L", (9, 8), 0)
    for y in range(8):
        for x in range(9):
            right.putpixel((x, y), 255 if x < 4 else 0)
    a, b = dhash_frame(left), dhash_frame(right)
    assert a != b and hamming(a, b) > 0


def test_duration_mismatch_demotes_but_does_not_destroy():
    """A different cut of the same footage should still surface for review."""
    frames = [0x1122334455667788] * 12
    s, detail = score(fp(frames, 9.1, LOUD), fp(frames, 30.0, LOUD))
    assert detail["same_cut"] is False
    assert 0.45 <= s, "a re-cut of the same footage should still be reviewable"
