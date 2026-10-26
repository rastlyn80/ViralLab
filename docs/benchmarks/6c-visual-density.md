# Phase 6C Part 5 — what `text_region_score` is actually measuring

A three-arm correlational study on the owner's 48-publication retention
population. Everything here is `[PRIMARY]` — measured on the real corpus on
2026-10-26 — and everything here is **correlational**. Nothing in this
document is causal, and the design cannot make it so (CLAUDE.md §3.4).

Reproduce with `server/features/visual_structure.py`.

---

## 1. The question, and why it was worth asking

Phase 6A found that `text_region_score` — the deterministic proxy built to
detect burned-in captions — correlates **0.01** with `ocr_word_count`. It
was never measuring text. Phase 6A deleted the claim but left the puzzle:
the proxy still behaved like it meant something.

Part 5 asked what it is measuring, and whether that thing relates to
retention.

## 2. Four of the six candidates could not be measured

The brief named six candidates: keyboard visibility, sheet music, hand
close-ups, shot distance, general visual density, background complexity.

**The vision benchmark disqualified four of them.** From
`docs/benchmarks/6c-vision-benchmark.md`:

| field | specificity | verdict |
|---|---|---|
| `piano_or_keyboard_visible` | **0.000** (0 of 3) | reports a piano in a garden |
| `hands_visible` | **0.000** (0 of 3) | never correctly denies |
| `sheet_music_visible` | 0.235 (4 of 17) | reports sheet music in 13 openings without any |
| `shot_distance` | — | accuracy 0.304 vs 0.500 baseline, lift **−0.196** |

Correlating a near-constant, confabulated feature against retention would
manufacture a relationship out of the model's own bias. These four are
**withheld, not reported as null results** — they are unmeasured. The
refusal is encoded in `visual_structure.WITHHELD_VISION` with the measured
reason attached, so it is enforced rather than merely intended.

The two that remained — visual density and background complexity — are
deterministic image statistics and need no model at all. `edge_density`
already existed in `hookfeat`. Background complexity needed building.

### How background complexity is measured

`shared/hookbg.py`, method version `hook-background-v1`. Background is
identified **temporally**: across the four hook frames, pixels whose
standard deviation is below 6 grey levels are background, the rest are
subject. A pianist's hands move; the bookshelf behind them does not.

It fails loudly rather than quietly. When the camera moves, nothing is
static, no background can be identified, and the complexity measures are
reported `unavailable` with a reason rather than computed over a handful of
stray pixels. That happened on **2 of 48** publications, which is why some
rows below read n=46.

Measures: `background_static_fraction`, `background_edge_density`,
`background_grey_entropy`, `subject_edge_density`,
`background_subject_edge_ratio`. Gradients are taken on the whole frame and
then sampled through the mask — masking first would manufacture a hard edge
along the mask boundary and inflate the very number being measured.

## 3. Method

- **Population**: 48 own-account Instagram publications having both a
  `reels_skip_rate` observation and a preserved file. This is the real
  sample size — Phase 6A established it is 48, not the 258 preserved videos.
- **Outcome**: `reels_skip_rate`, the share who skipped. Lower is better.
  Chosen over `ig_reels_avg_watch_time` because that metric **includes
  replays** and is therefore not a completion fraction.
- **Statistic**: Spearman rank correlation. p from the Fisher
  transformation; q from Benjamini–Hochberg across each arm.
- **Controls**: duration and publication age, by second-order partial
  correlation.
- **Clustering**: repeated takes of one performance collapsed to a single
  observation, reported alongside the raw figure rather than instead of it.

## 4. Arm A — what the proxy tracks

Association between `text_region_score` and each visual-structure measure.

| feature | n | rho | q | effect |
|---|---|---|---|---|
| **`background_edge_density`** | 46 | **+0.523** | 0.002 | large |
| `background_static_fraction` | 48 | +0.422 | 0.013 | moderate |
| `edge_density` | 48 | +0.416 | 0.013 | moderate |
| `subject_edge_density` | 46 | +0.365 | 0.032 | moderate |
| `background_grey_entropy` | 46 | +0.362 | 0.032 | moderate |
| `contrast_rms` | 48 | +0.282 | 0.088 | small |
| `background_subject_edge_ratio` | 46 | +0.268 | 0.115 | small |
| `vision_text_overlay_present` | 48 | +0.188 | 0.269 | small |
| `saturation_mean` | 48 | −0.157 | 0.340 | small |
| `brightness_mean` | 48 | +0.086 | 0.574 | negligible |

Five of ten survive correction. **The proxy's strongest association is with
background edge density — stronger than with whole-frame density.** It is
substantially a background-busyness detector that was named a text detector.

## 5. Arm B — what tracks retention

| feature | n | rho | q | partial | clustered |
|---|---|---|---|---|---|
| **`text_region_score`** | 48 | **−0.563** | **<0.001** | −0.524 | −0.582 |
| `background_edge_density` | 46 | −0.460 | 0.009 | −0.415 | −0.498 |
| `edge_density` | 48 | −0.436 | 0.009 | −0.408 | −0.531 |
| `saturation_mean` | 48 | +0.354 | 0.044 | +0.353 | +0.338 |
| `subject_edge_density` | 46 | −0.292 | 0.088 | −0.284 | −0.373 |
| `background_grey_entropy` | 46 | −0.285 | 0.088 | −0.274 | −0.381 |
| `contrast_rms` | 48 | −0.283 | 0.088 | −0.190 | −0.310 |
| `background_subject_edge_ratio` | 46 | −0.219 | 0.201 | −0.136 | −0.217 |
| `vision_text_overlay_present` | 48 | −0.188 | 0.259 | −0.164 | −0.191 |
| `brightness_mean` | 48 | −0.180 | 0.262 | −0.136 | −0.238 |
| `background_static_fraction` | 48 | +0.059 | 0.700 | +0.081 | +0.018 |

Negative rho means **more of this, less skipping**. The density effects
survive the duration and age controls, and *strengthen* under clustering —
so they are not an artefact of a few repeated takes of one performance.

### The juxtaposition that matters

| | vs `reels_skip_rate` | vs `text_region_score` |
|---|---|---|
| `ocr_word_count` (real OCR) | **+0.011** (p = 0.94) | +0.012 |
| `ocr_element_count` | −0.167 (p = 0.27) | +0.063 |
| `ocr_max_text_height_frac` | +0.025 (p = 0.87) | −0.014 |
| `ocr_text_duration_s` | −0.276 (p = 0.065) | +0.361 |
| `ocr_text_changes` | +0.082 (p = 0.59) | −0.268 |
| `ocr_first_text_at_s` | −0.088 (p = 0.57) | −0.016 |
| **`text_region_score`** | **−0.563** (q < 0.001) | — |

**The measurement that is definitively not reading text is the strongest
retention correlate in the corpus; actual text measurement is noise against
the same outcome.** Whatever matters about these openings, it is not how
many words are on screen.

## 6. Arm C — head to head

Does background density explain the proxy, or do they merely overlap?
Partial correlation run **both** ways, because one direction alone cannot
distinguish "explained by" from "shares variance with".

| pair | n | rho(a,skip) | rho(b,skip) | rho(a,b) | a given b | b given a |
|---|---|---|---|---|---|---|
| `text_region_score` vs `background_edge_density` | 46 | −0.564 | −0.460 | +0.523 | **−0.428** | −0.234 |
| `text_region_score` vs `edge_density` | 48 | −0.563 | −0.436 | +0.416 | **−0.466** | −0.268 |

Controlling for background density, the proxy retains −0.428. Controlling
for the proxy, background density falls to −0.234. The background explains
**part** of the proxy's signal but not most of it: something in the proxy's
specific signature — dense, high-contrast, predominantly vertical edges
arranged in a horizontal band — carries more than generic edge density does.

At n = 46 the two are **not cleanly separable**, and the module says so
rather than declaring a winner.

## 7. What this does and does not license

**Established.** `text_region_score` is not a text measurement; it is
substantially a visual-structure measurement, and visual structure in the
opening is associated with lower skip rates on this account.

**Not established.** That adding visual clutter to an opening would reduce
skipping. That is a causal claim and this is a correlational study on 48
publications from one creator in one niche. Plausible confounds are
unexamined: busier openings may be concert footage while sparse ones are
home practice, and the audience may be responding to the occasion rather
than the pixels. `setting` would test that — and `setting` is one of the
fields the vision benchmark rated only partly reliable.

**Not a recommendation.** Nothing here may enter the recommendation model.
Per CLAUDE.md §3.6 that needs all five validation tests, and per §3.13 a
causal claim needs the Phase 8 pre-registered experiment. This result is a
**hypothesis worth pre-registering**, which is exactly what Phase 8 is for.

### Limitations, stated plainly

1. n = 48, one creator, one niche, one platform.
2. `reels_skip_rate` is observed once per publication at whatever age it
   was captured. Age is controlled for, which is weaker than comparing at a
   fixed anchor age (CLAUDE.md §3.8a).
3. Four of the six properties the brief named are **unmeasured**, not null.
4. The second-order partial correlation assumes monotone relationships and
   is run on ranks; it does not rescue a confound that was never measured.
5. `background_edge_density` is `unavailable` on 2 of 48 publications, both
   with camera motion. Those rows are excluded, not zero-filled.

## 8. Next

- Pre-register the direction for Phase 8: *openings with more visual
  structure show lower skip rates*. Pre-registration must precede
  publication, never follow it (§3.13).
- Make `setting` reliable enough to test the occasion confound. That needs
  a better vision model or a held-out prompt revision — see §6 of the vision
  benchmark.
- `[OPEN]` Does the relationship hold on TikTok? 234 TikTok publications
  became analysable on 2026-10-26 (ADR 0007) and have never been tested.

---

## Addendum, 2026-10-26 — the ad confound

After this document was written the owner disclosed a **$1/day Instagram
ad that rotates to each new post**, running for several months. Paid
exposure per video is therefore roughly proportional to the gap until the
next post, which is derivable from `posted_at`. On the 47 retention videos
with a gap `[PRIMARY]`:

| | rho | p |
|---|---|---|
| gap-to-next-post vs `reels_skip_rate` | −0.378 | 0.010 |
| gap-to-next-post vs `text_region_score` | +0.157 | 0.306 |
| gap-to-next-post vs `edge_density` | +0.341 | 0.022 |

| finding | raw | partial (gap controlled) |
|---|---|---|
| `text_region_score` vs skip | −0.558 | **−0.545** |
| `edge_density` vs skip | −0.435 | −0.352 |

The outcome is contaminated by ad exposure. The headline association
survives the control; the `edge_density` association partly does not. The
gap is a proxy — it also tracks organic accumulation — so this bounds the
confound rather than removing it. Separation of paid from organic exposure
is milestone M1 in `docs/BLUEPRINT.md`.
