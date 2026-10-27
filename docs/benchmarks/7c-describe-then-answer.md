# Does describing the frame first make the model look at it?

**Date:** 2026-10-27 · 40 own-account publications · RTX 2070 SUPER

The owner proposed the obvious alternative to forcing bare booleans
out of a captioning model: **have it describe the scene in detail and
take the answers from the description.** This tests that, and
separately tests whether the failure was the method or the model.

Two factors, crossed, everything else held identical — same frames,
same framing plan, same temperature, same scoring:

- **prompt**: `bare` (booleans only) vs `describe_first` (a required
  per-frame description, then the same booleans, with the model told
  the description is its only evidence)
- **model**: Gemma 3 4B Q4 (committed, 2.5 GB) vs Qwen2.5-VL 7B Q4
  (4.7 GB, peaked at 7857 MiB of 8192 — it fits, with nothing else
  resident)

Own-account only: competitor media was deleted under ADR 0009, and
re-buying it to answer a question about model behaviour would be
paying for precision this experiment does not need.

---

## Results

Cells are **specificity / lift**. Bars are 0.80 and +0.10.

| field | gemma bare | gemma describe | qwen bare | **qwen describe** |
|---|---|---|---|---|
| piano keys | 0.33/+0.05 | 0.33/+0.05 | 0.50/+0.05 | 0.40/+0.05 |
| hands | 0.00/+0.00 | 0.00/+0.00 | 0.33/−0.05 | 0.33/+0.00 |
| sheet music | 0.89/+0.20 | 0.89/+0.20 | 1.00/+0.15 | 1.00/+0.13 |
| **face** | 0.38/+0.03 | 0.29/−0.03 | 0.88/+0.18 | **0.86/+0.21** |
| text overlay | 1.00/+0.00 | 1.00/+0.03 | 1.00/−0.44 | 1.00/−0.08 |

| cell | time | answered | failures |
|---|---|---|---|
| gemma bare | 211 s | 40/40 | 0 |
| gemma describe | 461 s | 40/40 | 0 |
| qwen bare | 881 s | 39/40 | 1 |
| qwen describe | 350 s | 38/40 | 2 |

---

## 1. On a weak model, describing first changes nothing

Gemma's two columns are **identical** on piano, hands and sheet music
— not similar, identical — and slightly worse on faces, for 2.2× the
inference time. The reasoning step is decorative.

The failure inspection shows why, and it is worth seeing. Video 52,
truth: no keyboard in shot. Gemma's own descriptions:

> *Frame 1: A man with dark hair, glasses, and a black jacket is seen
> from the side, filling the frame. The background is dark and
> indistinct.*
> *Frame 3: A close-up of the man's face is shown, filling the frame.
> The background is dark.*

**No piano anywhere in its own description. It then answered
`piano_or_keyboard_visible: true`**, having been instructed in the
same prompt to use only its own descriptions. It does not read what
it wrote.

A second, different failure appears on videos 3 and 91, where the
description is accurate — *"a man seated at a black Steinway piano"* —
and the inference from it is still wrong, because "seated at a piano"
is read as "the keys are visible". **OWLv2 makes the identical
conflation**, which suggests the question is partly at fault and not
only the models; see §4.

## 2. On a stronger model, it helps — and the model matters more

Qwen is better almost everywhere, and the gain is largest exactly
where Gemma was worst:

- **face: 0.38 → 0.88 specificity, lift +0.03 → +0.21.** This is the
  single biggest improvement measured in the project.
- piano: 0.33 → 0.50. hands: 0.00 → 0.33. Better, still far short.
- sheet music: specificity reaches 1.00, but recall falls to 0.55.

Qwen says "no" far more readily, which is the behaviour change the
whole exercise was looking for. It buys that with misses rather than
with false alarms — a different operating point, not a free lunch.

**Describing first then earns its keep.** On Qwen it rescues text
overlay (lift −0.44 → −0.08, recall 0.53 → 0.89) and improves the
face lift to its best measured value, while halving the time of the
bare arm. So the owner's proposal is right, conditionally: *it works
on a model strong enough to actually use its own description.*

## 3. What still does not work

**Nothing clears both bars on piano or hands in any cell.** And the
reason remains the one from Phase 1: the own cohort holds roughly 6
and 3 negatives for them. These numbers are not a verdict; there is
no denominator to pass a verdict with.

Qwen also costs 1.7× to 4× Gemma's time and leaves 500 MiB of
headroom on the card, so nothing else may be resident while it runs.

## 4. A doubt about the question, not just the answer

Videos 3 and 91 were labelled "no keyboard visible", and both models
describe a performer *seated at a piano*. If someone is seated at the
instrument, the keys are plausibly in frame, and the label may simply
be wrong — 91 was already flagged label-uncertain in M3.

That matters more than one video. **"Can you see the piano keys?" may
be a poor question**: ambiguous at the boundary, hard to label
consistently, and of doubtful creative value even when answered
correctly. "Is this a performance shot or a talking-head?" is sharper
and more useful. Part of the measured failure is likely a disagreement
about the question rather than an error in answering it.

The cheapest high-value action available remains the owner confirming
or overturning the flagged-ambiguous labels. It is already recorded in
`DEVELOPMENT_PLAN.md` §11 and this experiment raises its value again.

## 5. What this does not decide

These are 40 publications from one cohort, used to compare
configurations — a **dev-set** exercise on a corpus already spent
twice. Nothing here promotes a field. The prospective holdout in
`7b-holdout-protocol.md` is still what any promotion must wait for.

No paid dependency was introduced. Qwen2.5-VL 7B is free, local, and
now the strongest configuration measured.
