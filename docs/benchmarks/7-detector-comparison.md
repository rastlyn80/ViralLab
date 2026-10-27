# Visual intelligence, Phase 1: which detector, and can we even tell?

**Date:** 2026-10-27 · **Hardware:** RTX 2070 SUPER, 8 GB · **Corpus:** the
80 labelled publications from M3 (40 own, 40 competitor), *already spent*

This is the comparative report the brief asked for before any larger
implementation. It benchmarks four approaches against the M3 baseline on
identical frames, and it reports one result that matters more than the
ranking: **for three of the five fields, this corpus cannot tell us
whether a detector works.**

---

## 1. The finding that reframes the exercise

The pre-registered bar is **specificity ≥ 0.80**. Specificity is measured
on negatives. Here is how many negatives exist:

| field | negatives, competitor | negatives, own | one error costs |
|---|---|---|---|
| `sheet_music_visible` | 29 | 28 | 0.034 |
| `face_visible` | 28 | 24 | 0.036 |
| `text_overlay_present` | 11 | **1** | **1.00** |
| `hands_visible` | 7 | **3** | **0.33** |
| `piano_or_keyboard_visible` | 4 | **6** | **0.25** |

With three negatives, the only attainable specificities are 0.00, 0.33,
0.67 and 1.00. A bar of 0.80 can be met *only by a perfect score*, and a
single mislabelled or borderline video decides the outcome. With one
negative the measurement is a coin.

This is not a detector failure. It is a corpus failure, and it is
structural: the held-out set was drawn from what M3 happened to sample,
and **a concert pianist's videos contain pianos and hands**. 70 of 80
publications show a keyboard; 70 of 80 show hands.

So the honest reading of every piano and hands number below — including
the ones that look like improvements — is *unmeasured*, not *failed*.
Phase 4's prospective holdout with deliberate negatives is therefore a
**precondition for judging these two fields at all**, not a refinement of
a judgement already made.

---

## 2. The comparison

Thresholds are tuned on one cohort and tested on the other, both
directions. The in-sample number is computed but marked as the one to
distrust. `*` clears **both** bars (specificity ≥ 0.80, lift ≥ +0.10).

| approach | dir | piano | hands | sheet music | face | text overlay |
|---|---|---|---|---|---|---|
| Gemma 3 4B (M3 baseline) | own→comp | 0.00/+0.00 | 0.00/+0.00 | 0.10/−0.38 | 0.86/+0.13 | 0.91/+0.25 `*` |
| | comp→own | 0.00/+0.00 | 0.00/+0.00 | 0.29/−0.20 | 0.75/+0.23 | 1.00/+0.03 |
| deterministic (hook-detect-v1) | own→comp | 0.50/−0.50 | — | 0.97/+0.00 | — | — |
| | comp→own | 0.67/−0.15 | — | 0.93/−0.03 | — | — |
| CLIP ViT-B/32 | own→comp | 0.00/−0.03 | 0.00/+0.00 | 1.00/+0.05 | 0.82/+0.05 | 0.27/+0.07 |
| | comp→own | 0.33/+0.05 | 0.00/−0.03 | 0.82/−0.03 | 0.88/+0.17 `*` | 1.00/−0.15 |
| **OWLv2-base** | own→comp | 0.25/+0.03 | 0.57/+0.07 | **0.90/+0.20** `*` | 0.96/+0.07 | — |
| | comp→own | 0.50/+0.05 | 0.33/−0.03 | **0.79/+0.10** | 0.25/−0.10 | — |
| OWLv2-large | own→comp | 0.25/+0.03 | 0.57/+0.10 | 1.00/+0.17 `*` | 0.86/+0.15 `*` | — |
| | comp→own | 0.50/+0.07 | 0.33/+0.03 | 0.89/+0.05 | 0.67/+0.12 | — |

Cells are `specificity / lift`. Gemma's figures reproduce the M3 report
exactly (face 0.857/0.750, text overlay 0.909/1.000, piano 0.000), which
is the harness validating itself against a published result.

### Cost

| approach | inference, 80 videos | cold load | peak VRAM |
|---|---|---|---|
| CLIP ViT-B/32 | 12.0 s | 7.9 s | 1 346 MiB |
| OWLv2-base | 118.8 s | 9.1 s | 2 601 MiB |
| OWLv2-large | 602.8 s | 20.5 s | 4 164 MiB |
| deterministic | 53.7 s | — | 0 (CPU) |

All four run locally on the 2070 SUPER. **No paid inference dependency
was introduced or needed.**

---

## 3. What the comparison supports

**Nothing clears both bars in both directions.** The closest is OWLv2-base
on sheet music — 0.90/+0.20 and 0.79/+0.10 — where the second direction
misses the specificity bar by 0.014, which is **less than one video**.
That is a near-miss, not a pass, and the pre-registered rule does not have
a category for near-misses.

**On sheet music the improvement over the baseline is nonetheless large
and real.** Gemma scores 0.10 and 0.29 specificity with *negative* lift:
it says "yes" to sheet music almost regardless of the image. OWLv2-base
scores 0.90 and 0.79 on 29 and 28 negatives. On the one field with a
sound denominator, open-vocabulary detection is categorically better than
the captioning VLM.

**OWLv2-large is not worth its cost.** 5.1× the inference time and 1.6×
the VRAM buy a better face number and a *lower* lift on sheet music. It
is rejected.

**CLIP is the wrong tool for localised objects**, as expected and now
measured: 0.00 specificity on both piano and hands. Whole-image zero-shot
classification cannot answer "is this part of the instrument visible".
Its one good cell (face, comp→own) does not replicate in the other
direction. The brief's caution against treating similarity scores as
calibrated probabilities is upheld — these scores are ranked, not
calibrated, and the harness records that in `score_semantics`.

**The deterministic detectors do not address these questions at all.**
Negative lift on piano. They measure edge density and colour, and were
never claimed to detect objects; the benchmark confirms it rather than
assuming it.

**Per-field, not one model.** Gemma already clears text overlay
(0.91/+0.25) and is the best available on face. OWLv2 wins sheet music
decisively. Nothing can be said about piano or hands. The evidence
supports a **per-field assignment of method**, not a single replacement
detector — which is what Phase 2's specialized-analyzer design assumes,
and it is now measured rather than assumed.

---

## 4. A failed experiment, recorded

Inspecting OWLv2-base's piano failures showed the detector scoring a
grand piano shot side-on from the stalls (video 259, no keys in frame) at
0.459 and a piano filmed over its open soundboard (761, no keys) at
0.429, against true positives as low as 0.346. The highest-scoring
negative of all (707, 0.649) is dark abstract footage with no instrument
in it. Negatives median 0.398; positives median 0.549. The distributions
overlap almost completely.

Two of the top negatives (91, 700) are videos flagged *label-uncertain*
during M3 — a grand piano where keys may be marginally visible at the
frame edge, and a shot too distant to resolve them. On the two
unambiguous negatives the detector is decisive and correct (0.034,
0.017).

The diagnosis was that OWLv2 answers "is there a piano" when asked "is
there a piano keyboard". The remedy tried was a **discriminative query**:
score `"a piano keyboard"` and subtract `"a grand piano seen from the
side with no keys visible"`, `"the open lid and strings of a piano"`,
`"a concert stage photographed from far away"`.

**It did not work.** Specificity was unchanged own→comp (0.25) and
collapsed to 0.00 comp→own; lift went to −0.05. OWLv2 scores the positive
and negative phrasings on the *same box*, so the subtraction removes
signal from true positives as fast as from false ones. Recorded as a
negative result; the code path is kept behind `--owl-discriminative` so
the claim is reproducible, and it is **not** enabled by default.

---

## 5. What this does and does not decide

**Decided, on dev data:** OWLv2-base is the open-vocabulary detector to
build Phase 2 around — better than CLIP, than the deterministic
heuristics, and than Gemma on the only field with enough negatives to
judge, at a fifth of OWLv2-large's cost. This is a *model-selection*
decision, which is what a spent dev set is for.

**Not decided:** whether any feature may be promoted. These 80 videos
were marked spent the moment they were used to choose a prompt in M3, and
this report has now used them to choose a model and tune thresholds. No
number here may serve as a promotion decision for any feature.

Accordingly:

- `piano_or_keyboard_visible` and `hands_visible` **stay withheld**, and
  the reason is now sharper than "the detector is unreliable": *nobody
  has yet measured whether it is reliable*.
- `sheet_music_visible` is **not promoted** on the strength of
  0.90/+0.20, despite being the best result in the report.
- The new prospective holdout must carry **deliberate negatives** — piano
  videos with no keyboard in frame, performance footage with no hands
  visible, videos with no on-screen text — in both cohorts, or it will
  reproduce this exact dead end.

**Per ADR 0009** the cached competitor frames (36.9 MB) are deleted on
completion of this benchmark; the labels persist, the bytes do not.
Re-running this report costs another $0.16 of acquisition, which is the
standing price of the retention guarantee.

**Spend:** $2.2316 of $5.00 authorised. No new paid dependency.
