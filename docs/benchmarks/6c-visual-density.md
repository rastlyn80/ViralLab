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

## Addendum, 2026-10-26 — the ad confound measured, and the sample 5× larger

Two things happened after §1–§8 were written, and together they change the
conclusion of §6.

1. **The ad confound became measurable.** Milestone M1 derived a real
   paid-exposure covariate from Instagram's own surface metrics, replacing
   the gap-to-next-post proxy.
2. **The sample grew from 48 to 258.** Hook features existed for only 48 of
   the 258 preserved publications — the blueprint's claim of 258 was wrong
   — and extracting the missing 210 took nine minutes of CPU and cost
   nothing.

Everything in §1–§8 stands as *computed at n = 48*. The larger sample
contradicts part of it, and the contradiction is reported here rather than
by editing the original. Reproduce with
`scripts/provision/rerun_part5.py`.

### A.1 The covariate, and what it actually is

`[PRIMARY]` Meta documents Instagram's `views`, `likes`, `comments`,
`saved`, `shares` and `total_interactions` as reporting **organic
interactions only** — "interactions on ads containing a media object are
not counted" — while `total_views` spans "all surfaces, including views
from promoted/boosted/ad media and Facebook". Measured on this account,
`crossposted_views == views + facebook_views` exactly. So

    residual_surface_share = (total_views − crossposted_views) / total_views

is exposure that is neither organic Instagram nor Facebook. It is an
**upper bound** on the paid share, not the paid share itself. Full
verification, and the two ways it can fail to resolve, are in
`docs/adr/0008-paid-organic-separation.md`.

Coverage: **319 of 385** publications resolve; 66 refuse with a stated
reason (54 have zero total views, 12 cannot attribute their gap). On the
258-publication analysis population, **247 resolve and 24 are boosted** at
a residual share ≥ 0.25.

That last figure is the most important caveat in this addendum. The
covariate is zero for roughly nine in ten of the sample, so the
ad-controlled partials below are carried by 24 publications. That is a
low-powered control wearing the same notation as a well-powered one.

### A.2 The campaign window, and a correction to the record

Collecting the residual for all **385** own-account publications dates the
boosting without relying on recollection:

| | |
|---|---|
| publications resolved | 373 of 385 |
| residual exactly 0 | 338 |
| residual share ≥ 0.25 | 29 |
| median share among the non-zero | 0.74 |
| first continuous month | **2026-07** |

The distribution is bimodal — a post is either promoted or it is not.
Three isolated boosts precede the campaign (2025-06-17, 2026-03-07,
2026-06-30); the rest run continuously from **2026-07-13**. August boosted
7 of 40 publications, so the ad does **not** rotate to every new post.

This corrects `docs/BLUEPRINT.md` §1, which recorded "several months" and
"rotates to each new post", and the owner's later recollection of "a few
weeks". The campaign is about three and a half months old and selective.
`[PRIMARY, 2026-10-26]`

### A.3 The contamination diagnostic — it fires on every outcome

Association between the ad-exposure covariate and each outcome, n = 247:

| outcome | rho | p |
|---|---|---|
| `reels_skip_rate` | **−0.303** | <0.001 |
| `shares_per_view` | **+0.388** | <0.001 |
| `saves_per_view` | **+0.376** | <0.001 |
| `comments_per_view` | **+0.298** | <0.001 |
| `ig_reels_avg_watch_time` | +0.250 | <0.001 |
| `likes_per_view` | +0.190 | 0.004 |

Boosted publications are skipped less, watched longer and engaged with
more, on every metric.

**At n = 48 `ig_reels_avg_watch_time` appeared unrelated to the covariate**
(rho +0.143, p = 0.354) and this document's first version called it "the
cleanest of the six with respect to this confound". At n = 247 it is
related like the rest. That claim is withdrawn: **no outcome is clean of
this confound.**

**It cannot be direct metric contamination** for the engagement rates,
whose numerator and denominator are both documented organic-only. Two
explanations remain and **this design cannot separate them**:

1. **Selection.** The owner chooses which posts to boost, and would
   plausibly choose ones already doing well. The association then runs from
   quality to boosting, not from boosting to measured quality.
2. **Spillover.** Ad exposure sends people to the profile and the organic
   post, where their engagement is counted organically.

Under reading 1 the control is an over-correction — it removes real
quality. Both the controlled and uncontrolled figures are therefore
reported throughout and neither is called the answer.

### A.4 Arm B at n = 258, against the published n = 48

`+dur+age` is the second-order partial published in §5; `+ad` adds ad
exposure as a third control.

| feature | n=48 rho | **n=258 rho** | q | +dur+age | **+ad** | n(ad) | clustered |
|---|---|---|---|---|---|---|---|
| `background_edge_density` | −0.459 | **−0.401** | <0.001 | −0.336 | −0.318 | 229 | −0.394 |
| `edge_density` | −0.435 | **−0.389** | <0.001 | −0.358 | −0.334 | 247 | −0.386 |
| `background_grey_entropy` | −0.282 | **−0.329** | <0.001 | −0.260 | −0.267 | 229 | −0.317 |
| `text_region_score` | −0.564 | **−0.292** | <0.001 | −0.267 | −0.248 | 247 | −0.316 |
| `brightness_mean` | −0.178 | **−0.252** | <0.001 | −0.145 | −0.175 | 247 | −0.217 |
| `saturation_mean` | +0.350 | **+0.247** | <0.001 | +0.247 | +0.257 | 247 | +0.258 |
| `subject_edge_density` | −0.291 | **−0.244** | <0.001 | −0.232 | −0.233 | 229 | −0.236 |
| `contrast_rms` | −0.284 | **−0.226** | 0.001 | −0.106 | −0.124 | 247 | −0.186 |

Nine of eleven features now survive correction, against four before —
mostly a power effect, not a discovery.

**The headline estimate was inflated by the small sample.**
`text_region_score` falls from −0.564 to **−0.292**, and from first place
to fourth. This is what regression to the mean looks like: the n = 48
estimate was the largest of eleven correlations drawn from a small sample,
and the largest such estimate is the one most likely to be too large.
Nothing was done wrong at n = 48; the number was simply optimistic, and
only more data could show it.

The density measures are more stable: `edge_density` −0.435 → −0.389,
`background_edge_density` −0.459 → −0.401.

### A.5 Arm C reverses — §6's conclusion does not survive

§6 ran the head-to-head both ways at n = 46, found the proxy ahead
(−0.428 against −0.234), and declined to declare a winner because "at
n = 46 the two are **not cleanly separable**". That caution was right, and
the larger sample resolves it the other way.

| pair | n | rho(a) | rho(b) | a given b | **b given a** | a given b, +ad | b given a, +ad |
|---|---|---|---|---|---|---|---|
| `text_region_score` vs `background_edge_density` | 239 | −0.271 | −0.401 | **−0.096** | −0.320 | −0.079 | −0.283 |
| `text_region_score` vs `edge_density` | 258 | −0.292 | −0.389 | **−0.124** | −0.293 | −0.112 | −0.255 |

At n = 258 the proxy collapses to −0.096 and −0.124 once density is
controlled, while density holds at −0.320 and −0.293 once the proxy is
controlled. **Background and edge density carry the association; the
proxy is a noisier reading of the same quantity.**

So §6's "something in the proxy's specific signature carries more than
generic edge density does" is **withdrawn**. It was an artefact of n = 46.
Arm A agrees: at n = 258 the proxy correlates +0.497 with `edge_density`
and +0.483 with `background_edge_density` (both q < 0.001), which is what
"a noisier reading of the same thing" looks like.

What survives from §7 unchanged: `text_region_score` is **not** a text
measurement, and visual structure in the opening is associated with lower
skip rates on this account. What changes is which measurement to prefer —
the deterministic density measures, not the proxy.

### A.6 The outcome family — retention is not the only thing worth measuring

Added at the owner's request (2026-10-26): per-viewer engagement rates
matter more than views, and are already paid-free on both sides of the
ratio. Survivors at q < 0.05 **within each outcome**, n = 258, with the ad
control applied.

This table lists the **strongest survivors per outcome, not all of them**
— 30 feature-outcome pairs clear q < 0.05 in total, and listing every one
would bury the pattern. The full set is in the JSON that
`rerun_part5.py` emits.

| outcome | feature | rho | q | +dur+age | +ad |
|---|---|---|---|---|---|
| `reels_skip_rate` | `edge_density` | −0.389 | <0.001 | −0.358 | −0.334 |
| | `text_region_score` | −0.292 | <0.001 | −0.267 | −0.248 |
| | `brightness_mean` | −0.252 | <0.001 | −0.145 | −0.175 |
| | `saturation_mean` | +0.247 | <0.001 | +0.247 | +0.257 |
| | `contrast_rms` | −0.226 | 0.001 | −0.106 | −0.124 |
| | `visual_novelty_vs_later` | +0.224 | 0.001 | +0.191 | +0.190 |
| `ig_reels_avg_watch_time` | `text_region_score` | +0.330 | <0.001 | +0.300 | +0.293 |
| | `edge_density` | +0.309 | <0.001 | +0.246 | +0.204 |
| | `brightness_mean` | +0.282 | <0.001 | +0.137 | +0.157 |
| | `saturation_mean` | −0.269 | <0.001 | −0.242 | −0.246 |
| `saves_per_view` | `edge_density` | +0.316 | <0.001 | +0.266 | +0.241 |
| | `text_region_score` | +0.279 | <0.001 | +0.245 | +0.238 |
| | `visual_novelty_vs_later` | −0.243 | <0.001 | −0.204 | −0.202 |
| `shares_per_view` | `text_region_score` | +0.290 | <0.001 | +0.255 | +0.229 |
| | `edge_density` | +0.286 | <0.001 | +0.228 | +0.162 |
| `likes_per_view` | `edge_density` | +0.180 | 0.037 | +0.201 | +0.144 |
| `comments_per_view` | *(none survive)* | | | | |

**The directions agree across five outcomes.** For skip rate lower is
better; for the other four higher is better. `edge_density` and
`text_region_score` go the beneficial way on every one of the five, and
`saturation_mean` and `visual_novelty_vs_later` go the harmful way on
every one they appear in. Five outcomes are not five independent tests —
they are correlated measurements of the same audience behaviour — but a
feature that reversed sign between them would be a strong signal of noise,
and none does.

`comments_per_view` yields nothing, which is unsurprising: commenting is
the rarest action here and the rate is dominated by small-count noise.

**Multiplicity across outcomes is NOT controlled.** Benjamini–Hochberg is
applied within each outcome's family of 15 features; six outcomes is 90
tests and the FDR guarantee does not extend across them. The borderline
rows — `likes_per_view` at q = 0.037, `shares_per_view`'s weaker entries —
are weaker than their q-values look.

### A.7 Why the sample grew, and what it cost

Two separate coverage gaps, both closed on 2026-10-26, both discovered by
M1 rather than designed for:

- **Retention metrics had never been requested** for most of the back
  catalogue. The population carrying a `reels_skip_rate` went from 49 to
  **286**; 183 publications gained one that day, and every one had exactly
  one prior snapshot carrying no `retention_status` field at all.
- **Hook features existed for 48 videos, not 258.** `docs/BLUEPRINT.md` §0
  claimed "21 visual/audio measures on 258 videos"; `video_features` held
  `hook-deterministic-v1`, `hook-ocr-v1`, `hook-background-v1` and
  `hook-speech-v2` for 48 each. Extracting the missing 210 took **290 s**
  for the deterministic set and **95 s** for background, on the server's
  CPU, with **0 failures** and no API calls. Coverage is now 258 of 258.

**OCR was then backfilled too**, which was possible because RapidOCR runs
on CPU where the media already lives — it never needed the GPU box. 207
publications, **0 failures**, 2277 features, 856 s. Coverage is now 258 of
258 for the deterministic, background *and* OCR sets.

**Speech (48) and vision (54) remain**, and those genuinely need the
Windows worker. They are now the only coverage gap left, and the binding
constraint on any speech- or model-derived finding.

One defect was found and fixed in the process: `spectral_centroid_hz`
divided by a spectrum sum that is exactly zero on a silent clip, producing
NaN, which was then stored as an ordinary number. One row was affected and
has been re-measured. The audit that nearly missed it is worth recording:
**Postgres treats `NaN = NaN` as TRUE**, so `where value_num != value_num`
matches nothing and reports a clean table. The correct predicate is
`where value_num = 'NaN'::float8`. Both the cause and a storage-level
guard are now covered by regression tests.

### A.7a §5's juxtaposition re-run at n = 258 — it holds

§5's central juxtaposition was the sharpest claim in this document: the
measurement that is definitively *not* reading text is the retention
correlate, while actual text measurement is noise against the same
outcome. It was computed on 48 publications. At 258:

| | proxy vs this OCR measure | proxy vs skip, controlling for it |
|---|---|---|
| `ocr_word_count` | +0.029 | −0.291 |
| `ocr_element_count` | +0.087 | −0.282 |
| `ocr_text_duration_s` | +0.218 | −0.264 |
| `ocr_text_changes` | −0.142 | −0.303 |

`text_region_score` vs `reels_skip_rate` is **−0.292** uncontrolled.
Controlling for every real OCR measurement moves it to between −0.264 and
−0.303 — that is, not at all.

**The claim survives the 5× sample.** The proxy correlates at most +0.22
with any genuine text measurement, and no OCR measurement explains its
association with retention. Combined with §A.5, the fuller statement is
now: the proxy is not reading text, *and* what it is reading is generic
visual density, which measures the same thing better.

One caveat changed with the sample and was being asserted rather than
computed: the module hardcoded "96% of these publications carry on-screen
text (46 of 48)". It is now derived, and reads **97% (249 of 258)** — the
substance is unchanged, but a hardcoded sample statistic in a module that
gets re-run on new data is a stale claim waiting to happen, and it has
been replaced with the calculation.

### A.8 What this does and does not license, restated

**Established, at n = 258 on one creator's Instagram account.** Visual
structure in the opening — background and whole-frame edge density — is
associated with lower skip rates, longer average watch time, and higher
save and share rates. The association survives duration, age and
ad-exposure controls, and survives collapsing repeated takes.
`text_region_score` is confirmed, at 5× the original sample, not to be
measuring text.

**Withdrawn.** That `text_region_score` carries signal beyond generic
density (§6). That `ig_reels_avg_watch_time` is uncontaminated by ad
exposure (this addendum's first version).

**Still not established.** Any causal claim. Any transfer to another
creator, niche or platform. That the owner's boosting choices are not
themselves the explanation for §A.3. `setting` remains unmeasured, so the
occasion confound of §7 is untested.

**Still not a recommendation.** CLAUDE.md §3.6 requires all five
validation tests before a model claim, and §3.13 requires the
pre-registered Phase 8 experiment before a causal one. The pre-registered
direction should now name **`edge_density` or `background_edge_density`**
rather than `text_region_score` — the larger sample says those are the
measurements with the signal.

**The cheapest strong test available next is TikTok.** 225 own TikTok
publications have preserved media (ADR 0007) and have never had a feature
extracted. The same CPU-only extractors would run on them for free, in
about fifteen minutes. TikTok has no retention metric, so `skip rate`
cannot be replicated — but the per-viewer **engagement rates** can, and
TikTok is reportedly unboosted, which makes it a natural control for the
confound §A.3 cannot resolve on Instagram. A density–engagement
association that reproduces on a second platform, under a different
algorithm and without the ad, would be considerably stronger evidence
than anything in this document. It is `[OPEN]` and it is one script away.

### A.9 Superseded: the gap-to-next-post proxy

Kept for the record. Before the surface decomposition existed, paid
exposure was proxied by days until the next publication:

| | rho | p |
|---|---|---|
| gap vs `reels_skip_rate` | −0.378 | 0.010 |
| gap vs `text_region_score` | +0.157 | 0.306 |
| gap vs `edge_density` | +0.341 | 0.022 |

| finding | n=48 raw | gap-controlled | measured covariate, n=48 | **measured, n=258** |
|---|---|---|---|---|
| `text_region_score` vs skip | −0.558 | −0.545 | −0.528 | **−0.248** |
| `edge_density` vs skip | −0.435 | −0.352 | −0.279 | **−0.334** |

The proxy was computed over 47 publications almost none of which was
actually boosted — the campaign began in July 2026 and most of that corpus
predates it — so what it mostly measured was organic accumulation, exactly
as its own caveat warned. It is retained in
`server/analytics/ad_exposure.py` only as a last-resort fallback, and is
never mixed with measured values in one column.
