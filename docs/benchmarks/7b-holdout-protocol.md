# The prospective holdout: pre-registered protocol

**Written 2026-10-27, before any video was sampled or labelled.**
Pre-registration is worth nothing written afterwards, so the order
matters: this document fixes the sampling rule, the labelling rule,
the estimator and the promotion bar, and only then is anything
collected.

---

## 1. What went wrong, and why this is not just "more labels"

Phase 1 could not measure the piano or hands questions at all. The
80-publication corpus contains 4 and 6 negatives for
`piano_or_keyboard_visible` and 7 and 3 for `hands_visible`. With
three negatives the attainable specificities are 0, 0.33, 0.67 and
1.00; a bar of 0.80 can be met only by a perfect score.

The obvious fix — collect videos without pianos — does not work, and
the reason is the most useful thing learned today:

> **The negatives are not non-piano videos. They are piano videos
> whose opening does not show the keyboard.**

Their captions read `#piano #chopin`, `Ballade No.1`, `#pianotok`.
They are wide concert shots, faces, title cards, an orchestral render
captioned "my orchestration, no piano". Three independent signal
families were tested as a way of finding them cheaply and all three
failed:

| signal | piano visible | piano NOT visible |
|---|---|---|
| edge density | 0.114 | 0.090 |
| background static fraction | 0.581 | 0.613 |
| brightness | 56.2 | 49.3 |
| first sound at (s) | 0.070 | 0.055 |
| OCR word count | 0 | 0 |
| spoken words | 0 | 0 |

No separation. Metadata describes the *video*; the question is about
the *opening frames*. **There is no shortcut around looking.**

---

## 2. Sampling rule, fixed in advance

Random sampling at a 12.5% negative base rate would need ~240 labelled
videos to yield 30 negatives. Instead, **stratify on the detector's
score and weight the result.**

1. Score every eligible publication with OWLv2-base (local, free) for
   each field under test. The score is a *ranking*, not a probability,
   and is used here only to form strata.
2. Divide into **5 equal-width strata** over the observed score range.
3. Sample **24 publications per stratum at random**, giving **n = 120**.
   Where a stratum holds fewer than 24, take all of it and record the
   shortfall.
4. Record each publication's stratum and its sampling fraction.

**Eligible** means: has frames obtainable, is not one of the 80 spent
publications, and belongs to the owner's account or a cohort creator.
Both cohorts are sampled; the own/competitor split is recorded per
stratum and reported, never pooled away.

### Why stratifying on the detector under test is legitimate here

Selecting negatives by the detector's *low* scores would be circular —
the holdout would contain only the negatives the detector already
gets right, and specificity would be inflated. **Sampling across the
whole score range, including the strata where the detector is most
confident, is the opposite.** It deliberately includes the cases most
likely to be false positives, which is where specificity is won or
lost.

The estimator below then removes the sampling design from the answer.

---

## 3. Estimator, fixed in advance

Each sampled publication carries weight `w = N_stratum / n_stratum`,
the number of eligible publications in its stratum over the number
sampled from it. This follows CLAUDE.md §3.11: a stratified sample
carries its weights or it silently misstates base rates.

| quantity | how it is computed | affected by the enrichment? |
|---|---|---|
| **specificity** | unweighted over sampled negatives | **no** |
| **recall** | unweighted over sampled positives | **no** |
| precision | weighted | yes — must be reweighted |
| accuracy | weighted | yes — must be reweighted |
| base rate / lift | weighted | yes — must be reweighted |

The distinction is the point. **Specificity and recall are conditional
on the true label**, so over-representing negatives does not bias
them: P(detector says no | truly no) is the same quantity however many
true negatives are sampled. Precision, accuracy and therefore lift all
depend on prevalence and are reweighted to the eligible population's
real base rate before anything is compared to a bar.

Confidence intervals are reported on every proportion. With 40
negatives, a specificity of 0.80 carries roughly ±0.12 at 95%, and
that interval is reported rather than the point estimate alone.

---

## 4. Labelling rule, fixed in advance

- Labels are assigned from the **same frames the detectors see**, so a
  disagreement is about judgement and not about different images.
- Labelling happens **before** any detector output for that
  publication is consulted. The stratum is known — it has to be, to
  sample — but the score is not shown to the labeller.
- Genuinely ambiguous cases are **labelled and flagged**, not dropped.
  Dropping hard cases is the single easiest way to manufacture a good
  specificity. Flagged cases are reported as a sensitivity analysis:
  the bar must be cleared both with them and without them.
- `piano_or_keyboard_visible` means **the keys are visible**, not that
  a piano is present. This is exactly the distinction OWLv2 currently
  fails, so it is written down before labelling rather than argued
  about after.
- Labels are written by Claude. **This is a known weakness** already
  recorded in `DEVELOPMENT_PLAN.md` §11 for the M3 set, and it is not
  fixed by doing it again. The flagged-ambiguous list is the cheapest
  thing the owner could overturn to strengthen every figure.

### On using a paid model

A paid vision model may label, **or** it may be a candidate detector.
It may not be both: a model that writes the answer key and sits the
exam proves nothing. No paid dependency is introduced by this protocol
as written; if one is proposed later it enters on the labelling side
only, with the cost stated and approved first.

---

## 5. Promotion bar, unchanged and pre-registered

A field is promoted from `indicative` to `verified` only if, on this
holdout:

- **specificity ≥ 0.80**, and
- **reweighted lift ≥ +0.10** over the reweighted base rate, and
- both hold **with and without** the flagged-ambiguous publications.

Thresholds are **not** tuned on this holdout. Any threshold a detector
needs is fixed beforehand on the spent M3 corpus, which is what a spent
corpus is still good for. A detector arriving without a pre-fixed
threshold is not eligible.

A field that fails stays withheld. **It is not promoted for beating
Gemma**, which is a low bar and not the bar.

This holdout is spent the moment it is used to choose anything. It is
for promotion decisions only, and a test will mark it `_spent_by` on
first use, as the M3 fixtures are.

---

## 6. What this protocol does not fix

- **Interpretive questions remain unverifiable.** Mood, formality, how
  an opening "presents" — there is no ground truth for a holdout to
  check, so these stay `indicative` permanently by design, not by
  neglect.
- **One labeller.** Inter-rater agreement cannot be computed from a
  single rater, so no agreement statistic is reported rather than a
  misleading one.
- **TikTok-weighted.** The cohort is TikTok-only for the reasons in
  §11 of the plan, so the competitor arm says nothing about Instagram.
- **n = 120 is a compromise.** It buys roughly ±0.12 on a specificity
  near the bar. It does not buy a tight estimate, and a field landing
  between 0.80 and 0.85 should be read as "not clearly above the bar"
  rather than as a pass.

---

## 7. Addendum, 2026-11-13 — pre-registration of the scoring run

**Written before the holdout was scored, and committed on its own so
that "before" is checkable in the history rather than asserted.** The
answer key was completed by the owner on 2026-11-12: 111 publications
x 3 fields = 333 answers. Nothing below was chosen with any knowledge
of how the detector does against them.

Five things sections 1-6 left open, fixed now.

### 7.1 The frame set — a defect found and corrected before scoring

The stratification scores in `holdout_scores.json` were computed on
**4 frames** (`sample_holdout.py frames --frames 4`, timestamps 0, 1,
2, 3 s). The owner labelled from what `/api/labels/frames/{id}`
serves, which is `FramePlan().timestamps(duration)[:MAX_FRAMES]` —
**up to 6 frames**, adding 5 s and 12 s. The M3 corpus that fixed
every threshold was scored on that same 6-frame set.

An OWLv2 score is the highest box confidence **maximised over
frames**, so a 4-frame score is systematically lower than a 6-frame
score on the same video. Scoring the holdout from
`holdout_scores.json` would therefore compare a threshold fixed in one
frame regime against scores taken in another, and would break section
4's requirement that the labeller and the detector look at the same
images. The partial read in `score_holdout_partial.py` did exactly
this and its figures are withdrawn on that ground, independently of
what they said.

**The arm under test is therefore re-scored on the frames the owner
judged** — `FramePlan[:MAX_FRAMES]`, width 512, same encoder. The
4-frame scores keep the one role they were ever given: forming strata.

This does not invalidate the design. Selection depended only on the
stratum, the selection probability per stratum is known and positive
everywhere, so inverse-probability weighting stays unbiased however
weakly the stratum variable correlates with the arm. A noisier
stratum variable spreads the negatives less efficiently; it does not
bias anything.

### 7.2 Which threshold, fixed now

Thresholds come from the spent M3 corpus, per section 5, and
`tests/fixtures/vision/detector_comparison.json` holds three per
field. The choice is declared here:

| field | primary (decides promotion) | pre-declared secondary |
|---|---|---|
| `piano_or_keyboard_visible` | **0.3778** (tuned on competitor) | 0.3117 (tuned on own) |
| `hands_visible` | **0.1285** (tuned on competitor) | 0.0755 (tuned on own) |

The primary is the competitor-tuned threshold because it is the only
one no own-account footage helped choose, and this holdout is
own-account (7.4). The own-tuned threshold is reported beside it as a
declared sensitivity, not as a second chance: **promotion is decided
on the primary alone**, and both are reported whatever they say. The
`in_sample_optimistic` thresholds are not eligible.

### 7.3 `cannot_tell` answers

The owner answered `cannot_tell` on 6 of the 333 — 4 hands, 2 piano.
These are section 4's flagged-ambiguous cases arriving as a
non-answer rather than a flagged answer, so section 5's "with and
without" becomes:

- **primary**: `cannot_tell` videos excluded from that field.
- **sensitivity**: each `cannot_tell` assigned the label that hurts
  the detector most, given what it predicted. The bar must clear
  this too.

A worst-case assignment is used rather than a best-case one because
the question is whether the bar is cleared, not whether it can be.

### 7.4 One cohort, and what that costs

The eligible pool is 443 publications, **all the owner's own**:
`sample_holdout.py frames` selects on `is_own_account is true`,
because ADR 0009 deletes competitor media and a holdout cannot be
drawn from files that no longer exist. Section 2's "both cohorts are
sampled" was not met and cannot be met from this draw.

Consequence, stated before the result: **a field clearing both bars
here is promoted for own-account use only.** It says nothing about
unseen creators, and under ADR 0011's replication bar it is
`Observed`, not `Validated`. A competitor half costs a fresh
acquisition run and is a separate decision.

### 7.5 The both-halves rule, now written down

Carried over from `DEVELOPMENT_PLAN.md` section 11, which required
this before M4's first measurement: **from M4 onwards a field is
promoted only if it clears the bar on each cohort separately, not
pooled.** A field passing pooled while failing the unseen-creator
half has not shown it generalises; one passing pooled while failing
the own-account half is useless for recommendations. It is not
applied retroactively (CLAUDE.md 3.13).

On this holdout the rule has one half to apply to, which is 7.4's cap
and not a way around it.

### 7.6 Fields actually under test

OWLv2 scores four queries — piano, hands, sheet music, face — and
**no text-overlay query**. `text_overlay_present` therefore has no
arm on this holdout at all, and the owner answered yes on 107 of 111,
leaving 4 negatives. It is reported as **unmeasured**, which is what
it is, and not as a failure. `sheet_music_visible` and `face_visible`
were not asked of the owner here and are untouched by this run.

So: **two fields are under test, piano and hands.**

### 7.7 Estimator, as implemented

- Weights are regenerated from `holdout_scores.json` by
  `sample_holdout.py draw`, which is seeded (20261027) and reproduces
  the labelled set of 111 exactly — verified before scoring.
- Specificity and recall: unweighted, Wilson 95% intervals.
- Base rate, precision, accuracy, lift: weighted by stratum.
- Lift is weighted accuracy minus the weighted accuracy of answering
  the majority class every time — the constant answerer a detector
  has to beat before it is worth loading.
- Intervals on the weighted quantities come from a stratified
  bootstrap, resampling within strata, 2000 replicates, percentile
  interval.
