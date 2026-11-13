# The prospective holdout, scored: nothing is promoted

**Run 2026-11-13 against the answer key the owner finished on
2026-11-12.** Protocol and bars in
[`7b-holdout-protocol.md`](7b-holdout-protocol.md), including the
addendum committed before this run so that every choice below is
checkable as having been made first. Numbers in
`tests/fixtures/vision/holdout_result.json`, asserted by
`tests/server/test_holdout_result.py` so this page cannot drift from
the data.

**This holdout is now spent.** It has been used to decide promotion
and may not decide anything else.

---

## 1. The result

| field | specificity | lift | bar | outcome |
|---|---|---|---|---|
| `piano_or_keyboard_visible` | **0.926** [0.766, 0.979] | **+0.060** [+0.004, +0.112] | 0.80 / +0.10 | specificity clears, lift does not — **not promoted** |
| `hands_visible` | **1.000** [0.796, 1.000] | **−0.011** [−0.087, +0.063] | 0.80 / +0.10 | specificity clears, lift is negative — **not promoted** |
| `text_overlay_present` | — | — | — | **unmeasured**: OWLv2 has no text-overlay query, and 4 negatives in 111 |

Both fields stay withheld. That is the third phase in a row to promote
nothing, and this time the reason is different and worth reading.

---

## 2. A defect, found before scoring rather than after

The stratification scores were computed on **4 frames** (0, 1, 2, 3 s).
The owner labelled from what the review screen serves — up to **6
frames**, adding 5 s and 12 s — and the M3 corpus that fixed every
threshold was scored on 6 as well. An OWLv2 score is a maximum over
frames, so those are not the same quantity.

It was not a rounding difference:

| field | mean 6-frame minus 4-frame score | publications changing side of the threshold |
|---|---|---|
| `piano_or_keyboard_visible` | +0.041 | **9 of 111** |
| `hands_visible` | +0.029 | **8 of 111** |

Eight percent of the holdout would have been scored on the wrong side
of the line, and the labels would have been describing images the
detector never saw — the one thing section 4 of the protocol exists to
prevent. The arm was re-scored on the frames the owner judged.
`score_holdout_partial.py`'s earlier figures are withdrawn on that
ground, independently of what they said.

---

## 3. The finding that reframes the question

A lift bar is a claim about a population, not only about a detector.
On this corpus, weighted back to the 443 eligible publications:

| field | weighted base rate | best possible lift, for a **flawless** detector |
|---|---|---|
| `piano_or_keyboard_visible` | 0.851 | +0.149 |
| `hands_visible` | 0.912 | **+0.088** |
| `text_overlay_present` | 0.988 | **+0.013** |

**The +0.10 lift bar is unreachable for `hands_visible` by any
detector that could ever exist**, perfect ones included, because
answering "yes" every time is already right 91.2% of the time. The
same is true, far more sharply, for `text_overlay_present`: 98.8% of
the owner's publications carry on-screen text, so the whole question
is worth at most 1.3 points of accuracy on his account.

This is not a detector failure and it is not a corpus failure. It is
the bar and the question being mismatched. A measurement that is true
of 91% of the corpus cannot distinguish much inside it, whoever
answers it — and the recommendation model is the thing that needed
the distinction.

`piano_or_keyboard_visible` is the one of the three where the bar is
reachable, and OWLv2 reached 40% of the way to it.

---

## 4. What the holdout did establish

The thing it was built for. Before this run, piano specificity rested
on 4 and 6 negatives and hands on 7 and 3, and
[`7-detector-comparison.md`](7-detector-comparison.md) had to record
both as *unmeasured rather than failed*. They are now measured:

- **`piano_or_keyboard_visible`: specificity 0.926 on 27 negatives**
  (25 correct denials, 2 false positives), with recall 0.842 and
  weighted precision 0.962. The detector is good at saying no. The
  earlier picture — "OWLv2 answers *is there a piano* when asked *is
  there a keyboard*" — does not survive contact with a proper frame
  set and 27 negatives.
- **`hands_visible`: specificity 1.000 on 15 negatives**, with zero
  false positives at the primary threshold. It pays for that with
  recall 0.804 — 18 publications where hands are visible and it says
  no.

Both results are better than anything the spent corpus suggested. Both
still fail, for the reason in section 3 rather than for being wrong.

The pre-declared secondary thresholds, reported because they were
declared and not because they help: piano 0.889 / +0.072, hands 0.600
/ +0.049. Neither clears either. The adversarial treatment of the 6
`cannot_tell` answers moves piano to 0.893 / +0.056 and hands to
1.000 / −0.024, so no conclusion here depends on how those are
handled.

---

## 5. Limits, stated plainly

- **One cohort.** All 443 eligible publications are the owner's own,
  because ADR 0009 deletes competitor media. Nothing here speaks to
  unseen creators, and under ADR 0011 a result from one account is
  `Observed`, never `Validated`. Had a field cleared both bars it
  would have been promoted for own-account use only.
- **One labeller**, so no inter-rater agreement is reported rather
  than a misleading one. The independent check that does exist is the
  review of the 21 flagged-ambiguous M3 labels, where the owner
  overturned 5 and called 2 undecidable — the measured error rate of
  the labelling process on its own hardest cases, and the first
  evidence of its kind in this project.
- **n = 111**, short of the planned 120 because the top stratum held
  only 15 eligible publications. The intervals are wide: piano's
  specificity interval runs from 0.766 to 0.979, which straddles the
  bar it appears to clear.
- **Thresholds came from the spent M3 corpus** and were not tuned
  here. Nothing on this page is in-sample.

---

## 6. What follows

- Both fields stay withheld in
  `server/features/visual_structure.WITHHELD_VISION`, with the OWLv2
  measurement recorded beside the older VLM figures rather than
  replacing them — they measure different producers of the same field.
- **The lift bar needs rethinking before the next holdout is drawn,
  not after.** Two of three fields cannot reach it on this corpus at
  any level of skill. Keeping a bar that a perfect detector fails is
  how a project spends holdouts to learn nothing.
- `vision_text_overlay_present` is currently **promoted** and is 98.8%
  constant on the owner's account. It clears its M3 bar on a pooled
  corpus whose competitor half carried the negatives. That is not a
  contradiction, but it does mean the feature can contribute almost
  nothing to own-account recommendation, and it is recorded in
  `DEVELOPMENT_PLAN.md` section 11 as an open question rather than
  quietly demoted here.
- A competitor half remains the only route to a generalisation claim,
  and it costs a fresh acquisition run under ADR 0010.

**Spend:** none. OWLv2-base is local and free; the run took 169.5 s on
the RTX 2070 SUPER at 1422 MiB of VRAM. No paid dependency was
introduced.
