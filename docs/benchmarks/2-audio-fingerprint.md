# Benchmark: audio fingerprinting for cross-posted video matching

**Date:** 2026-10-24
**Machine:** `Gabriele-Studio` (Windows 11, Python 3.12)
**Code:** `probes/local_library/audiofp.py`, benchmark harness
`probes/local_library/benchmark_audiofp.py`
**Status:** new method **adopted**; the 32-bin envelope is retained only as a
fallback for records written before this existed.

## Why this was re-examined

The first matching pass used a 32-bin loudness envelope. It was added to fix a
real failure — frame hashing alone produced ~10 confident false auto-links
across the "musical portraits" series, which is visually identical and differs
only in the music — and it did reduce that to 1.

But the owner then supplied a fact that changes the requirement: **~99% of
videos are cross-posted between TikTok and Instagram**, with deliberate
differences in the opening hook, the on-screen text, and occasional trimming.
So the matcher's normal case is now *the same recording, differently edited*.
The envelope is structurally bad at exactly that:

- it compares **absolute** positions, so a 1-second trim misaligns every bucket
- it normalises to its own peak, so **shortening** the clip changes every value
- it describes loudness only, so two different piano pieces with similar
  dynamics look alike

## Method under test

A sub-Chromaprint design, numpy only — no model files, no training, no ML
dependency (`[PRIMARY]` own implementation):

1. decode to 11025 Hz mono (piano energy sits well below 5 kHz)
2. frame at ~93 ms with 50% overlap
3. FFT, fold into 16 log-spaced bands over 80–5000 Hz
4. emit one 16-bit word per frame, each bit the **sign of a band-energy change**
   relative to the previous frame

Using signs of *changes* is what buys robustness: absolute level cancels, so
loudness normalisation and encoder gain are free. The output is a *sequence*,
so comparison is a sliding alignment and a trim costs only the frames it
removes.

Two frames count as "the same moment" at Hamming distance ≤ 4 of 16 bits; a
match needs ≥ 12 aligned frames so two short silences cannot score 1.0.

## Ground truth

**Known positives (n=24)** — four real source files, each transformed the way a
platform or an edit would transform it. The result *is* the same recording, so
a correct fingerprint must say so.

**Known negatives (n=10)** — real, and the hardest in the archive: four
unrelated performances pairwise, plus consecutive members of the portrait
series (visually identical, ~9 s, different music).

One pair was **excluded as mislabelled**: `Beethoven - Improved` vs
`Beethoven - Test 2` scored 1.000 on *both* methods. Two files whose names
share a stem that way are two exports of one recording, so scoring them 1.000
is correct behaviour, not a failure. Left in, that single pair set
`max(negative) = 1.000` and made both methods look non-separable. The harness
now excludes same-stem pairs and says so in its output.

## Results

Per-transform, worst case across the four sources:

| transform | what it simulates | **new (spectral)** | old (envelope) |
|---|---|---|---|
| `reencode_low` | 48 kbit/s re-encode | **0.862** | 0.908 |
| `offset_1s` | 1 s start shift | **0.999** | 0.782 |
| `trim_head_3s` | **different opening hook** | **0.992** | 0.563 |
| `shorten_half` | **trimmed to half length** | **1.000** | 0.583 |
| `normalised` | loudness normalisation | **0.988** | 0.739 |
| `resampled` | 22.05 kHz / 64 kbit/s | **0.996** | 0.925 |

Separation:

| | positives min | positives median | negatives max | negatives median | **gap** |
|---|---|---|---|---|---|
| **new (spectral)** | **0.862** | 0.999 | **0.425** | 0.294 | **+0.437 — separable** |
| old (envelope) | 0.563 | 0.796 | 0.799 | 0.537 | −0.236 — overlapping |

The two rows that matter most for this archive are `trim_head_3s` and
`shorten_half` — a different hook and a trimmed clip, i.e. precisely the
cross-posting differences the owner described. The old method scored genuine
matches **0.563–0.781** there, *below* what it scored on true negatives
(up to 0.799). It could not have distinguished a cross-post from an unrelated
performance. The new method scores the same pairs 0.992–1.000 while pushing the
portrait-series negatives down from 0.747–0.799 to 0.423–0.425.

`reencode_low` is the one transform where the old method scores marginally
higher (0.908 vs 0.862). That is not a defect: 0.862 is far above the 0.425
worst negative, so the pair is still unambiguously separable.

## Threshold

`SPECTRAL_FLOOR = 0.70`, set inside the measured gap with margin on both sides
(worst positive 0.862, worst negative 0.425). This is a **decision boundary
calibrated on 34 labelled pairs, not a probability**. A score of 0.70 does not
mean "70% likely correct", and nothing in the system treats it that way: a
score alone never promotes a link to verified status.

## Two defects found and fixed during the benchmark

1. **Sub-frame misalignment.** `offset_1s` first scored **0.448** while
   correctly reporting a −1.02 s offset — alignment was right, the score was
   not. Cause: an arbitrary trim is not a whole number of hops, so every FFT
   window straddled different audio. Fix: store the fingerprint at
   `PHASES = 2` sub-frame phases and take the best pairing. That one change
   took `offset_1s` from 0.448 to 0.999 and `trim_head_3s` from 0.597 to 0.992,
   and is what turned the gap from −0.552 to +0.437.
2. **The mislabelled negative** described above.

## Cost

Measured on `Gabriele-Studio` after vectorising the per-frame loop (a 0/1 band
matrix and one batched FFT) and the per-offset loop (one strided window matrix
instead of a Python loop):

| operation | cost |
|---|---|
| fingerprint 65 s of audio | **0.14 s** |
| one full-fidelity pair comparison | **7.6 ms** |
| one screening comparison (`quick=True`, 30 s cap, primary phase) | **1.4 ms** |
| full sweep, 251 masters × 258 Instagram clips | **~8 min** |

Vectorisation was verified to be **byte-identical** to the pre-vectorisation
benchmark output, so the numbers above describe the same algorithm that was
measured.

## Corpus coverage

| corpus | fingerprinted | failed | note |
|---|---|---|---|
| local masters | **250 / 251** | 1 | `cloud_overlay.mp4` — an overlay asset with no audio track |
| Instagram clips | **258 / 258** | 0 | computed on the server; no media transferred |

The single failure is recorded as `audiofp_status = "decode_failed"` rather
than as a zero score, so an unverifiable pair stays distinguishable from a
pair that was checked and disagreed.

## Limits — what this does not establish

- `[ASSUMPTION]` Positives are **synthetic** transforms of real files. They
  model re-encoding, trimming and normalisation faithfully, but not a genuine
  platform re-encode by TikTok or Instagram, which is unavailable without
  publishing a test video.
- n=34 labelled pairs is enough to place a threshold inside a wide gap; it is
  **not** enough to estimate a false-positive rate to a decimal place.
- Negatives are all solo piano from one performer — the hardest realistic case
  for this archive, but a narrow one.
- `[OPEN]` No test covers two different performances **of the same work** by
  this performer (e.g. two takes of the same Chopin étude). That is the most
  plausible remaining confusion and needs labelled examples the owner
  identifies.
