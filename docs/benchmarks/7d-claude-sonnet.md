# Claude Sonnet on the questions nothing else could answer

**Date:** 2026-10-27 · 40 own-account publications · **billed $0.66**

The owner proposed a stronger model and authorised roughly $0.65 to
test it. This is the result, and it is the first unambiguous success
in this line of work — which is exactly why the caveats in §3 matter
more than the table in §1.

---

## 1. The measurement

`describe_first` prompt, identical frames, identical labels, identical
scoring to `7c-describe-then-answer.md`.

| field | specificity | recall | lift | negatives | FP | FN |
|---|---|---|---|---|---|---|
| **piano keys** | **1.000** | **1.000** | +0.150 | 6 | 0 | 0 |
| **hands** | **1.000** | **1.000** | +0.075 | 3 | 0 | 0 |
| sheet music | 0.857 | 0.750 | +0.125 | 28 | 4 | 3 |
| face | 0.833 | 1.000 | +0.300 | 24 | 4 | 0 |
| text overlay | 1.000 | 1.000 | +0.025 | 1 | 0 | 0 |

**Zero errors on all 40 publications for piano, hands and text
overlay.** Against the field that has defeated every previous
approach:

| approach | piano specificity |
|---|---|
| Gemma 3 4B (committed) | 0.00 – 0.33 |
| deterministic heuristics | 0.50, negative lift |
| CLIP ViT-B/32 | 0.00 |
| OWLv2-base | 0.25 – 0.50 |
| Qwen2.5-VL 7B | 0.40 – 0.50 |
| **Claude Sonnet** | **1.000** |

It also answered video 91 correctly — one of the publications flagged
label-uncertain in M3 and a case both OWLv2 and Gemma got wrong.

### Why it works, qualitatively

Video 3, truth: no keys in shot.

- **Gemma:** *"A man is **seated at** a black Yamaha piano, with a red
  backdrop behind him."* → answered piano visible. Wrong on the pose,
  wrong on the brand, wrong on the backdrop.
- **Sonnet:** *"A man **stands on a stage next to** a black grand
  piano, one arm raised, with a large blue screen behind him. The
  audience is seated in the foreground."* → answered correctly.

Video 52, truth: no keys in shot. Gemma described a face and no piano,
then answered piano visible anyway — it did not read its own
description. Sonnet described *"a man shown from the chest up looking
downward against a black background"* and answered accordingly.

So the owner's two proposals were both right, and they compound: the
describe-first structure only pays off on a model strong enough to
(a) describe accurately and (b) actually use what it wrote.

### Cost, measured not estimated

| | |
|---|---|
| per publication | **$0.0183** (600 output tokens) |
| this run, 40 publications | **$0.66** |
| whole library, 975 publications | **~$17.85** `[SECONDARY]` |
| Haiku 4.5 equivalent | roughly one third `[SECONDARY]`, untested |

Every figure is settled from the token counts the API returned. Seven
publications were re-run after truncation and **cost nothing extra**:
the idempotency key matched calls already settled, which is the guard
working as designed.

Total project spend is now **$2.89 of $5.00**.

---

## 2. A biased dropout, found and fixed

The first pass answered **33 of 40**; seven returned no parseable JSON.
That was truncation, not refusal — long descriptions hit the 600-token
cap before the JSON closed.

**This mattered.** Complex scenes produce longer descriptions, and
complex scenes are the hard cases; video 91, a known-ambiguous piano
negative, was among the seven. Reporting 33/40 with perfect
specificity would have been reporting a score with the hard cases
quietly removed.

The seven were re-run at 1400 output tokens and all answered. The
table in §1 is the full 40. The two passes differ only in how much
room the reply had, not in what was asked, but it is a configuration
difference and is recorded rather than smoothed over.

---

## 3. Why this is not yet a promotion

**The labels were written by Claude.** Sonnet scoring 40/40 against an
answer key written by another Claude model is the circularity this
project warned about before spending anything: *a model may label or
it may be measured, not both.* The two are not identical — a different
model, a different prompt, labels written months apart from the same
frames — but their errors are plausibly correlated, and a perfect
score is exactly the shape a correlated-error artefact takes.

**This is now the binding constraint on the whole programme.** Every
cheaper problem has been solved; what remains is that nobody
independent has checked the answers.

**The denominators are still tiny.** Six negatives for piano, three
for hands, one for text overlay. A perfect 3-of-3 has a 95% interval
of roughly [0.29, 1.00]. "No errors" on three cases is encouraging and
is not a specificity of 1.00.

**The corpus is spent twice over** — used to choose a prompt in M3 and
a model in Phase 1, and now a provider. Nothing here promotes any
field; `vision_questions.py` is unchanged and
`piano_or_keyboard_visible` remains withheld.

**One cohort.** Own-account only. Competitor media was deleted under
ADR 0009 and the competitor arm is untested for this provider.

---

## 4. What follows

1. **The owner confirms or overturns the flagged-ambiguous labels.**
   This has been the cheapest high-value action for three milestones
   and is now the one thing standing between a promising result and a
   trustworthy one. Roughly 20 publications.
2. **The prospective holdout** of `7b-holdout-protocol.md`, labelled
   with human anchoring rather than by Claude alone.
3. **Test Haiku 4.5** before committing to Sonnet in production. At
   roughly a third of the cost it may be sufficient, and that is a
   $0.21 question, not a $17.85 one.
4. **A decision about where the money goes.** Running Sonnet across
   the library costs about $17.85 against a $5.00 monthly cap. That is
   a separate authorisation and is **not** granted by this benchmark;
   the current `cloud_vision` cap of $1.00 would refuse it.
