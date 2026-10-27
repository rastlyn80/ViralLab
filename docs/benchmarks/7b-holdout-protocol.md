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
