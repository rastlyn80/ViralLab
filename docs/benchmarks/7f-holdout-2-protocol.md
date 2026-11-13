# Holdout 2: face and sheet music, both halves

**Written 2026-11-13, before any video was drawn or labelled, and
committed on its own so the order is checkable.** It inherits
everything from [`7b-holdout-protocol.md`](7b-holdout-protocol.md)
and its addendum; only the differences are written here.

---

## 1. Why these two questions and no others

Holdout 1 measured piano, hands and text overlay on the owner's
account and promoted nothing. The reason was not the detector:

| question | share of his publications answering yes | best lift a **flawless** answerer could score |
|---|---|---|
| text overlay | 98.8% | +0.013 |
| hands | 91.2% | +0.088 |
| piano / keyboard | 85.1% | +0.149 |

Two of the three bars were unreachable by anything. A concert
pianist's videos contain pianos and hands, and asking whether they do
cannot separate one of his videos from another.

The two visual questions that **do** vary on his account have never
been tested against anything:

| question | yes on his 40 M3 publications | yes on 40 other creators' |
|---|---|---|
| `face_visible` | 40% | 30% |
| `sheet_music_visible` | 30% | 28% |

Both leave room for a detector to be right or wrong, on both cohorts.
And both showed a flicker against performance on 40 publications
(`DEVELOPMENT_PLAN.md` §11, 2026-11-13) — same direction on TikTok and
Instagram, far too small to mean anything, and the reason this round
is worth an afternoon of his time rather than another three.

Piano, hands and text overlay are **not asked again**. They are
settled as questions about his account.

## 2. The attainability pre-check, which is new

**A field may not enter a holdout unless the lift bar is reachable on
the cohort being drawn.** With an expected base rate `p`, a flawless
answerer scores lift `1 − max(p, 1 − p)`; if that is below the bar,
the field cannot pass however good the detector is, and drawing a
holdout for it spends labelling effort to learn nothing.

Checked against the measured M3 base rates: face leaves +0.60 own and
+0.70 competitor, sheet music +0.70 and +0.72, against a +0.10 bar.
Both pass with room to spare. Piano, hands and text overlay fail it,
which is the lesson of holdout 1 expressed as a rule rather than a
regret.

Implemented as `score_holdout.lift_bar_is_attainable` and enforced by
`tests/server/test_holdout_design.py`, so the next round cannot repeat
holdout 1 by accident.

## 3. Both halves, which is the point of this round

Holdout 1 had one cohort and could therefore never have produced a
generalisation claim. This one draws **80 of his publications and 80
of other creators'**, and reports them **separately, never pooled**.
Per §7.5 of the 7b addendum, a field is promoted only if it clears
both bars on **each half**:

- clearing the own half alone means it works for him and is unproven
  elsewhere — usable for his recommendations, not a general claim;
- clearing the competitor half alone means it generalises and is
  unproven on him — not usable for his recommendations;
- clearing both is the first thing this project could call
  replicated under ADR 0011, and it is still `Observed`, because
  replication across cohorts is not a cause.

## 4. Sampling: no stratification this time, and why

Holdout 1 stratified on the detector's score because his videos were
nearly all positives and the negatives had to be hunted for. That is a
remedy for scarcity. Here both fields sit near 30–40% on both cohorts,
so negatives are not scarce, and a **simple random draw** is both
honest and simpler: no enrichment, therefore no weights, therefore
no reweighting of precision, accuracy or lift.

- **Own half:** eligible is every own publication with preserved media
  **minus the 80 M3 publications**, which are spent for these two
  fields — `face_visible` was promoted on them and the prompt was
  chosen on them. The 111 of holdout 1 **are** eligible: nothing about
  face or sheet music was ever read from them, and the new draw does
  not inherit holdout 1's strata. Draw 80 at random, seeded.
- **Competitor half:** 80 public videos drawn across the cohort's
  creators by `competitor_holdout.py`, which does not stratify for the
  same reason.

If a half comes back lopsided anyway, that is a finding to record and
stratify against next time — not something to fix after seeing it.

## 5. Thresholds, fixed now

From the spent M3 corpus, same rule as §7.2: the deciding threshold
for a half is the one tuned on the **other** cohort, so no half is
judged by a threshold its own creators helped choose.

| field | own half decided at | competitor half decided at |
|---|---|---|
| `face_visible` | **0.0879** (tuned on competitor) | **0.1709** (tuned on own) |
| `sheet_music_visible` | **0.3603** (tuned on competitor) | **0.3167** (tuned on own) |

`in_sample_optimistic` thresholds remain ineligible. No threshold is
tuned on this holdout.

## 6. Frames, inherited as a standing rule

The arm is scored on **the frames the labeller is shown** —
`FramePlan().timestamps(duration)[:MAX_FRAMES]`, width 512 — because
an OWLv2 score is a maximum over frames and a different frame set is
a different quantity. This cost holdout 1 a re-scoring run; it is a
rule now, not a lesson.

## 7. The six-hour window is the competitor design

ADR 0009: a frame strip is media bytes and third-party media is not
retained. So the competitor half is **acquired, extracted, labelled
inside the window, released**. The labels persist; the pixels do not.
Through the free `yt-dlp` route of ADR 0010 a re-fetch costs nothing,
so a video whose window closes before it is answered is not a loss —
it is fetched again. No credential, public content only, rate
limited, bounded per run.

Practically: the own half can be answered whenever. The competitor
half is started when the owner has an hour.

## 8. Unchanged from holdout 1

- Bars: specificity ≥ 0.80 **and** lift ≥ +0.10, on each half.
- `cannot_tell` excluded in the primary analysis, assigned
  adversarially in the sensitivity; the bar must clear both.
- Labels assigned blind: no model answer and no score reaches the
  interface.
- Wilson intervals on specificity and recall, and the interval is
  reported rather than the point estimate alone.
- Spent on first use, marked `_spent_by` in the fixture.
- One labeller, so no agreement statistic is invented.
