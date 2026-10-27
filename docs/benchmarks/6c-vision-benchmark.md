# Phase 6C — scene understanding on the real corpus

What a local vision-language model can and cannot tell ViralLab about the
opening seconds of a piano video, measured on 24 preserved Instagram
publications from @gabriele_baldocci's own account.

Everything below is `[PRIMARY]` — measured on this hardware and this corpus
on 2026-10-26 — unless tagged otherwise.

---

## 1. The headline, stated plainly

**Gemma 3 4B at Q4_K_M is reliable for exactly one of the eight scored
fields.** Against a baseline that answers every question with the
commonest label and never looks at a frame:

| | accuracy | majority baseline | lift |
|---|---|---|---|
| **All eight scored fields** | **0.761** | **0.781** | **−0.020** |

The model as a whole does not beat the constant answerer on this corpus.
That is the result, and it is reported first because the per-field table
below contains one genuinely good number that would otherwise be read as
representative.

CLAUDE.md §3.7 requires beating a baseline or saying plainly that you did
not. On the aggregate, it did not.

---

## 2. Setup

| | |
|---|---|
| Model | `gemma-3-4b-it-Q4_K_M.gguf` + `mmproj-model-f16.gguf` |
| Runtime | llama.cpp `llama-server` CUDA build b11898 |
| Decoding | grammar-constrained, `response_format: {"type":"json_schema"}` |
| GPU | RTX 2070 SUPER, 8192 MiB |
| Model VRAM | **4034 MiB** (1815 idle → 5849 loaded); peak during runs 5931 MiB |
| Frames per video | 4 hook frames (t=0,1,2,3s) + 2 context frames (t=5,12s) |
| Transport | Ubuntu media library → authenticated `/api/frames` → Windows worker |
| Latency | **7.88 s/video mean inference**, 9.9 s/video wall clock including transport |
| Tokens | 1878 input / ~224 output mean |
| Frame payload | ~370 KiB/video (8962 KiB for 24) |
| Cost | **$0.00** — no cloud provider was called |
| Schema validity | 55/55 responses valid, 0 invalid, 0 failed |
| Method version | `hook-vision-gemma3-4b-v2` |

Grammar-constrained decoding is not optional. The same task with
`response_format: {"type":"json_object"}` returned an object missing 14 of
17 required fields in 20.5 s; with the grammar it returns a valid object in
3.8 s. Measured once, on video 332, before the benchmark.

---

## 3. Per-field results

`sens` is sensitivity (of the openings that contained the thing, how many
were found). `spec` is specificity (of those that did not, how many the
model correctly denied). `lift` is accuracy minus the majority baseline.

| field | n | ok | wrong | unknown | acc | base | **lift** | sens | **spec** |
|---|---|---|---|---|---|---|---|---|---|
| `text_overlay_present` | 24 | 24 | 0 | 0 | **1.000** | 0.917 | **+0.083** | 1.000 | **1.000** |
| `face_visible` | 24 | 21 | 3 | 0 | 0.875 | 0.792 | +0.083 | 0.895 | 0.800 |
| `setting` | 24 | 15 | 6 | 3 | 0.714 | 0.542 | +0.172 | — | — |
| `hands_visible` | 24 | 21 | 3 | 0 | 0.875 | 0.875 | 0.000 | 1.000 | **0.000** |
| `piano_or_keyboard_visible` | 24 | 21 | 3 | 0 | 0.875 | 0.875 | 0.000 | 1.000 | **0.000** |
| `performer_visible` | 24 | 23 | 1 | 0 | 0.958 | 1.000 | −0.042 | 0.958 | — |
| `shot_distance` | 24 | 7 | 16 | 1 | 0.304 | 0.500 | **−0.196** | — | — |
| `sheet_music_visible` | 24 | 11 | 13 | 0 | 0.458 | 0.708 | **−0.250** | 1.000 | **0.235** |

### Specificity is the load-bearing column

Every opening in this corpus contains a performer; 21 of 24 contain a
piano; 22 of 24 contain creator-added text. Sensitivity is therefore cheap
— answering "true" unconditionally scores 1.000 on it. The only evidence
that the model looked at the pixels is whether it can say **no**.

- `piano_or_keyboard_visible`: **0 of 3** correct denials.
- `hands_visible`: **0 of 3**.
- `sheet_music_visible`: **4 of 17**.

On id514 — a man walking outdoors through a garden, no instrument in any
of the six frames — the model reported a piano *and* sheet music. On id237,
an audience filmed from behind, likewise. This is not a borderline call:
it is the model reciting what a pianist's video usually contains.

### What actually works

`text_overlay_present` is correct on all 24, including **both** openings
with no overlay, and including id453 and id456 where the text appears only
at t=2–3s. It also caught two overlays the first human pass had missed.
A 4B local VLM reading creator-added text presence perfectly, for free, in
8 seconds, is a real capability.

`face_visible` and `setting` are usable with care. Four of the six
`setting` errors are `studio` predicted against a `home_room` label, a
boundary the reference labels themselves call unreliable (§5).

### What is worse than guessing

`shot_distance` answered `medium` 16 times out of 23 and missed **every
one of the six wide shots**. A constant "close" would have scored 0.500
against its 0.304. Mode collapse, not measurement.

---

## 4. The schema defect this benchmark found

The first run stored `unknown` for `sheet_music_visible` on all 24 videos.
The cause was not model uncertainty:

> Under grammar-constrained decoding, **every required field was emitted
> 24/24 and every optional field 0/24** — the sole exception being
> `uncertain_fields`, which the prompt asks for by name.

`sheet_music_visible` was `required: false`, so the grammar permitted
omitting it and the model always did. It looked like 24 declined answers
and was really a question never asked.

Fixed in `server/ai/vision_task.py` by moving `sheet_music_visible` and
`scene_changes` into `required`, and the method version bumped to `v2` so
the v1 rows remain a correct record of what v1 asked. Re-running then
produced the 0.458 above — **asking the question honestly lowered the
aggregate score**, which is the cleanest demonstration available that the
earlier figure was an artefact.

Generalisable rule, recorded at the schema: with a grammar, `required` is
not a minimum, it is the whole answer.

---

## 5. How the reference labels were made, and what is wrong with them

Two passes exist. Both are in `tests/fixtures/vision/`.

**Pass one** (`human_labels.json`) was transcribed by eye from a contact
sheet, **before any inference was run** — the correct ordering. But it
recorded one frame per video (t=1.0s) while the model sees four. That left
47 disagreements of the form "model says true, the single labelled frame
says false" with nothing in the data to decide them.

**Pass two** (`human_labels_v2.json`) resolves them from the full frame
strips, labelling "visible in any of t=0,1,2,3". That window is not a
convenience: `vision_task.PROMPT` tells the model the 5s and 12s frames are
supplied *only* to judge whether the opening is representative, so scoring
presence on them would penalise the model for following instructions.

Pass two found pass one had **under-called presence in 14 of 24 videos** —
a performer at the piano missed at thumbnail size (id451), faces reflected
in a piano lid (id453, id456), a mouth-only close-up (id421).

### Three honest weaknesses in this benchmark

1. **Pass two was written after inference ran.** Pass one's guarantee does
   not transfer to it. The question asked of each strip was "is X visible
   in frames 0–3", which is a question about frames rather than about the
   model's answers, and the prediction table was not opened until the file
   was saved — but this is a weaker protocol than pass one's, and three
   videos (451, 237, 514) had been compared against predictions before the
   relabel started.
2. **n = 24, one creator, one niche.** Three negatives for piano and hands
   is thin evidence; `spec = 0.000` means 0 of 3, not 0 of 300.
3. **`setting` and `shot_distance` have ambiguous category boundaries.**
   `studio` vs `home_room` and `stage` vs `concert_hall` are not cleanly
   separable on this material. Some of that error is taxonomy, not vision.

`camera_angle` and `framing` are deliberately unscored: they are genuinely
ambiguous, and scoring them against one person's reading would report
agreement with that reading as if it were accuracy.

---

## 6. Consequences for the rest of ViralLab

**Usable as evidence now:** `vision_text_overlay_present`. Qualified:
`vision_face_visible`, `vision_setting`.

**Not usable:** `vision_piano_or_keyboard_visible`,
`vision_hands_visible`, `vision_sheet_music_visible`,
`vision_shot_distance`, `vision_performer_visible`. These are stored —
with their confidence, their frame hashes and this benchmark's verdict —
but they must not enter a correlation, a model feature set, or a
recommendation. A near-constant feature with zero specificity would
manufacture a relationship out of its own bias.

This directly constrains Phase 6C Part 5, which asked whether
`text_region_score` is associated with keyboard visibility, sheet music,
hand close-ups and shot distance. **The measurement says this model cannot
supply any of those four.** Visual density and background complexity are
deterministic image statistics and are measured without a model instead.

### Before any prompt revision

Tuning the prompt against these 24 and re-reporting on these 24 would be
overfitting. Any revision aimed at `shot_distance` or the presence
confabulation must be evaluated on a **freshly labelled held-out set** that
did not inform the change. Not done in Phase 6C.

### Open questions

- `[OPEN]` Does a larger local model (Gemma 3 12B at Q4 would need ~8 GB,
  at or past this card's ceiling) fix the presence confabulation, or is it
  intrinsic to asking a VLM a yes/no question about a strongly-expected
  object?
- `[OPEN]` Would a negative-example prompt ("if there is no piano, say
  false") raise specificity without destroying sensitivity? Requires a
  held-out set to answer.
- `[OPEN]` `text_overlay_present` is perfect here while RapidOCR
  (`hook-ocr-v1`) measures the same thing deterministically. Which is more
  reliable at scale is untested; they disagree on nothing in this set.

---

## 7. Reproducing

```bash
# Windows, with the server running and llama-server installed:
python -m gpu_worker.vision_worker --video-ids <ids> --limit 24 --force

# Scoring (server-side, reads video_features + ai_operations):
python -c "from server.features import vision_benchmark as vb; ..."
```

`server/features/vision_benchmark.py` carries the scoring logic and the
reasoning behind each column.

---

## Addendum, 2026-10-27 — the deterministic detectors, and they fail too

M3 proposed replacing the two worst-performing model fields with
geometric detectors, on the reasoning that "a piano keyboard is a
periodic black-and-white pattern and sheet music is five parallel
lines; both are likely detectable from pixels with far higher
specificity than a 4B language model. Measured, not assumed."

Measured. They are not.

`shared/hookdetect.py`, scored against the same 24 human reference
labels used above (`human_labels_v2.json`), with `hookfeat`'s frame
plan:

| field | n | recall | specificity | accuracy | baseline | lift |
|---|---|---|---|---|---|---|
| `piano_or_keyboard_visible` | 24 | **0.000** | 1.000 | 0.125 | 0.875 | **−0.750** |
| `sheet_music_visible` | 24 | **0.000** | 0.647 | 0.458 | 0.708 | **−0.250** |

The keyboard detector's specificity of 1.000 looks like an improvement
on the model's 0.000 and is not: it is the score for never answering
yes. It fired on none of the 21 publications containing a keyboard. The
sheet-music detector managed zero true positives and six false ones.

**Resolution was tested as a hypothesis and rejected.** At the default
160 px analysis width, white keys span about six pixels and adjacent
rows disagree about the period, so the "agreeing rows" run fragments to
one. That is a plausible starvation story, so it was tested at 480 px.
It got *worse*: agreeing-row counts reached 5 on positives against 2 on
negatives, where before the best positive reached 12. The distributions
overlap at both widths.

**Why — diagnosed on the single clearest case, not inferred.**
Publication 75 shows a grand piano keyboard filling the lower half of
the frame: large, high-contrast, evenly lit, close to front-parallel.
It is the easiest keyboard in the corpus. The detector finds **1 or 2
agreeing rows** on it at 160 px, 480 px and 720 px alike — the same as
it finds on a blank wall.

The reason is perspective. A keyboard photographed from a seated
player's position recedes, so the key spacing is not constant down the
image: each row crosses the keys at a slightly different pitch. The
detector requires adjacent rows to agree on a period within ±2 px
before it will call them a band, and perspective alone breaks that
agreement. Occlusion by hands and uneven lighting make it worse, but
they are not the cause — the cause is that **a keyboard in perspective
is not a constant-period grating row to row**, and the whole method
assumes it is.

The synthetic unit tests pass easily because a synthetic keyboard is
front-parallel by construction. That is exactly what makes them
worthless as evidence: they encode the assumption instead of testing
it. A fix would need a different algorithm — a 2D spectral method over
a region, or line detection on the key edges with rectification — not a
different threshold, and there is no evidence yet that either would
work.

**What was done about it.** `server/features/extract.run_detect` was
written and removed the same day: a pipeline stage that writes a field
no analysis may use is worse than no stage. The module and its eleven
tests are kept as the record of an approach that was tried and
measured. Both fields remain **withheld**, exactly as they were before.

**What this means for M3.** One of its four candidate routes is closed.
The remaining three are a negative-example prompt for Gemma 3 4B,
Qwen2.5-VL 7B Q4 on the RTX 2070 (measured in Phase 0B to fit at
~7.6 GB, co-residency impossible), and a cloud provider on own media
only once `cloud_vision` is funded — it is currently capped at $0.00.
The held-out set is still required before any of them may promote a
field, and the 24 labels above can no longer serve as it: they have now
been used to disqualify two different approaches.


---

## Addendum — M3: the held-out set, and what a prompt can and cannot fix

*2026-10-27. Supersedes every figure above for decision purposes. The
24-video tuning set has now been used to disqualify two approaches and
can no longer measure generalisation; these numbers come from labels
nobody had seen before inference ran.*

### The set

**80 publications. 40 own, 40 competitor, none of them among the 24.**

* The own half is drawn across the whole account rather than the newest
  40, so it is not one period of one style. Fixture:
  `tests/fixtures/vision/holdout_labels_own.json`.
* The competitor half is 40 TikTok publications from 33 different
  creators — the only unseen-creator evidence M3 has. Fixture:
  `tests/fixtures/vision/holdout_labels_competitor.json`. The media was
  acquired for **$0.1616** (40 files, $0.00404 each, measured), labelled
  inside its six-hour window, and deleted; ADR 0009 governs why the
  labels persist and the frames do not.
* **Both halves were labelled by Claude, not by the owner.** This is the
  single largest caveat on everything below. Each fixture lists the
  cases it flagged as genuinely ambiguous — 10 on the own half, 11 on
  the competitor half — and those are where a second opinion would
  change a number.
* Labels were written before any inference. No model or detector output
  was consulted.

### Why the competitor half changes what is measurable

The own half has **no negatives at all** for `performer_visible` and
exactly one for `text_overlay_present`. Nothing can be learned from a
field with no variance, and a lift bar is unreachable when the base rate
is 0.975. The competitor half supplies 5 and 11 respectively — including
four piano-roll animations with no person in shot, and one piece of dark
abstract footage with no instrument anywhere. Several of the figures
below exist only because that half exists.

### The arms

Two prompts, same model (Gemma 3 4B Q4_K_M), same GBNF grammar, same
schema, same frame plan, same JPEG bytes, run back to back on each
publication:

* **control** — `vision_task.PROMPT`, the prompt this pipeline runs.
* **treatment** — `vision_task.PROMPT_V2`, a negative-example prompt
  written to attack the specificity collapse: it names the base-rate
  shortcut explicitly, gives a visibility criterion per field ("TRUE
  only if you can see piano KEYS", "White paper is not notation"), and
  supplies the measured false positives, the garden among them, as
  worked negatives.

Holding everything but the prompt constant is what makes the difference
attributable to the prompt. Note that **latency is not comparable
between the arms** — the second arm reuses llama.cpp's warm image cache,
which is why it runs *faster* despite a prompt 591 tokens longer.

### Result, pooled over all 80 — the population M3's rule names

| field | arm | n | spec | TN/neg | recall | acc | baseline | lift | clears |
|---|---|---|---|---|---|---|---|---|---|
| `performer_visible` | original | 80 | 1.000 | 5/5 | 0.920 | 0.925 | 0.938 | -0.012 | no |
| `performer_visible` | negative_example | 80 | 0.400 | 2/5 | 1.000 | 0.963 | 0.938 | +0.025 | no |
| `piano_or_keyboard_visible` | original | 80 | 0.100 | 1/10 | 1.000 | 0.887 | 0.875 | +0.012 | no |
| `piano_or_keyboard_visible` | negative_example | 80 | 0.200 | 2/10 | 1.000 | 0.900 | 0.875 | +0.025 | no |
| `hands_visible` | original | 80 | 0.100 | 1/10 | 1.000 | 0.887 | 0.875 | +0.012 | no |
| `hands_visible` | negative_example | 80 | 0.000 | 0/10 | 1.000 | 0.875 | 0.875 | +0.000 | no |
| `face_visible` | original | 80 | 0.808 | 42/52 | 0.857 | 0.825 | 0.650 | +0.175 | **yes** |
| `face_visible` | negative_example | 80 | 0.750 | 39/52 | 1.000 | 0.838 | 0.650 | +0.188 | no |
| `sheet_music_visible` | original | 80 | 0.193 | 11/57 | 1.000 | 0.425 | 0.713 | -0.288 | no |
| `sheet_music_visible` | negative_example | 80 | 0.807 | 46/57 | 0.913 | 0.838 | 0.713 | +0.125 | **yes** |
| `text_overlay_present` | original | 80 | 0.917 | 11/12 | 1.000 | 0.988 | 0.850 | +0.138 | **yes** |
| `text_overlay_present` | negative_example | 80 | 1.000 | 12/12 | 0.912 | 0.925 | 0.850 | +0.075 | no |

| field | arm | answered/labelled | declined | acc | baseline | lift |
|---|---|---|---|---|---|---|
| `shot_distance` | original | 79/79 | 0 | 0.418 | 0.519 | -0.101 |
| `shot_distance` | negative_example | 79/79 | 0 | 0.494 | 0.519 | -0.025 |
| `setting` | original | 58/69 | 11 | 0.362 | 0.681 | -0.319 |
| `setting` | negative_example | 67/69 | 2 | 0.194 | 0.681 | -0.487 |

**M3's rule, written down before any of this was measured: a field
enters analytical use at specificity >= 0.80 and lift >= +0.10 on the
held-out set.** Applied to the prompt actually in production:

* **`vision_text_overlay_present` — confirmed.** Already promoted from
  the 24-video benchmark; it holds on fresh held-out data at 0.917 /
  +0.138. This is the one field in the vision stack with two
  independent passes behind it.
* **`vision_face_visible` — promoted, with the caveat attached to it in
  the registry.** 0.808 / +0.175 pooled. Per half it is 0.857 on
  competitor publications and **0.750 on the owner's own**, which is
  below the bar. The own half is the population any recommendation is
  actually about, so this is the weakest entry in `VISION_FEATURES`.
* Everything else stays withheld.

An earlier draft of the scorer required a field to clear the bar on
*both halves independently*. That is a stricter and probably better
rule — but M3 pre-registered "on the held-out set", and tightening a
threshold after seeing which fields it would exclude is the move
CLAUDE.md §3.13 exists to prevent. The pooled figure therefore decides,
and the per-half split is reported beside it as a diagnostic. Requiring
both halves should be written into M4 *before* its first measurement.

### The prompt did one thing, very well, and paid for it

| field | control only right | treatment only right | agreed | McNemar p |
|---|---|---|---|---|
| `performer_visible` | 0 | 3 | 37 | 0.250 |
| `piano_or_keyboard_visible` | 0 | 1 | 39 | 1.000 |
| `hands_visible` | 0 | 0 | 40 | — |
| `face_visible` | 3 | 2 | 35 | 1.000 |
| `sheet_music_visible` | 2 | 18 | 20 | 0.000 |
| `text_overlay_present` | 2 | 0 | 38 | 0.500 |

*(own half; `b` = control right and treatment wrong, `c` = the reverse)*

| field | control only right | treatment only right | agreed | McNemar p |
|---|---|---|---|---|
| `performer_visible` | 3 | 3 | 34 | 1.000 |
| `piano_or_keyboard_visible` | 1 | 1 | 38 | 1.000 |
| `hands_visible` | 1 | 0 | 39 | 1.000 |
| `face_visible` | 2 | 4 | 34 | 0.688 |
| `sheet_music_visible` | 0 | 17 | 23 | 0.000 |
| `text_overlay_present` | 4 | 1 | 35 | 0.375 |

*(competitor half)*

`sheet_music_visible` goes from **0.193 to 0.807** specificity pooled —
18 publications fixed against 2 broken on the own half (p = 0.0004), 17
against 0 on the competitor half (p < 0.001). That is not noise and it
is not a threshold nudge: the model genuinely stops calling blank music
stands, art books and numbered keys "notation" once it is told in so
many words that white paper is not notation.

And it is still **not promoted**, for a reason worth stating plainly.
The prompt does not sharpen the model's perception; it moves the model's
whole operating point towards "no". That is a trade, and the invoice
arrives on the fields that were already fine —

* `text_overlay_present` recall falls 1.000 → 0.912, dropping its lift
  from +0.138 to +0.075, below the bar;
* `face_visible` specificity falls 0.808 → 0.750, below the bar;
* `performer_visible` specificity collapses 1.000 → 0.400 — it begins
  claiming a performer in the piano-roll animations;
* `setting` accuracy halves, 0.362 → 0.194.

A prompt is selected per run, not per field. Adopting the treatment
prompt would gain one field and lose two, and picking the better arm for
each field *after* seeing this table would be selection on the test set.
So the pipeline keeps the control prompt
(`vision_task.DEFAULT_VARIANT = "original"`), and `sheet_music_visible`
stays withheld with a known route forward rather than a dead end:
either two inference passes, or a prompt that fixes notation without
shifting the global operating point. Which of those is worth paying for
is a cost question for M4.

### What the prompt could not fix at all

`piano_or_keyboard_visible` moved from specificity 0.100 to 0.200 — and
**the single case it fixed is the garden, the example written into the
prompt**. That is close enough to teaching the test to be worth saying
out loud. The ten negatives in 80 are a harder problem than the garden:
a grand piano shot side-on from the stalls, a piano filmed from above
its open soundboard, an orchestral score with no instrument in frame, a
face against black. The model answers *yes* to all of them, because it
is reasoning from "this is a piano video" rather than from the pixels,
and no amount of instruction dislodged that.

Together with the geometric detectors' failure recorded above, the
keyboard field has now been attacked three ways — a 4B VLM, a
negative-example prompt, and row-wise autocorrelation — and has not
moved. It is the hardest field in this task, not the easiest, which is
the opposite of what the M3 brief assumed.

### Per-half tables

Own (n=40):

| field | arm | n | spec | TN/neg | recall | acc | baseline | lift | clears |
|---|---|---|---|---|---|---|---|---|---|
| `performer_visible` | original | 40 | — | 0/0 | 0.925 | 0.925 | 1.000 | -0.075 | n/a |
| `performer_visible` | negative_example | 40 | — | 0/0 | 1.000 | 1.000 | 1.000 | +0.000 | n/a |
| `piano_or_keyboard_visible` | original | 40 | 0.000 | 0/6 | 1.000 | 0.850 | 0.850 | +0.000 | no |
| `piano_or_keyboard_visible` | negative_example | 40 | 0.167 | 1/6 | 1.000 | 0.875 | 0.850 | +0.025 | no |
| `hands_visible` | original | 40 | 0.000 | 0/3 | 1.000 | 0.925 | 0.925 | +0.000 | no |
| `hands_visible` | negative_example | 40 | 0.000 | 0/3 | 1.000 | 0.925 | 0.925 | +0.000 | no |
| `face_visible` | original | 40 | 0.750 | 18/24 | 0.938 | 0.825 | 0.600 | +0.225 | no |
| `face_visible` | negative_example | 40 | 0.667 | 16/24 | 1.000 | 0.800 | 0.600 | +0.200 | no |
| `sheet_music_visible` | original | 40 | 0.286 | 8/28 | 1.000 | 0.500 | 0.700 | -0.200 | no |
| `sheet_music_visible` | negative_example | 40 | 0.929 | 26/28 | 0.833 | 0.900 | 0.700 | +0.200 | **yes** |
| `text_overlay_present` | original | 40 | 1.000 | 1/1 | 1.000 | 1.000 | 0.975 | +0.025 | no |
| `text_overlay_present` | negative_example | 40 | 1.000 | 1/1 | 0.949 | 0.950 | 0.975 | -0.025 | no |

| field | arm | answered/labelled | declined | acc | baseline | lift |
|---|---|---|---|---|---|---|
| `shot_distance` | original | 40/40 | 0 | 0.375 | 0.525 | -0.150 |
| `shot_distance` | negative_example | 40/40 | 0 | 0.375 | 0.525 | -0.150 |
| `setting` | original | 36/39 | 3 | 0.278 | 0.667 | -0.389 |
| `setting` | negative_example | 38/39 | 1 | 0.132 | 0.667 | -0.535 |

Competitor (n=40):

| field | arm | n | spec | TN/neg | recall | acc | baseline | lift | clears |
|---|---|---|---|---|---|---|---|---|---|
| `performer_visible` | original | 40 | 1.000 | 5/5 | 0.914 | 0.925 | 0.875 | +0.050 | no |
| `performer_visible` | negative_example | 40 | 0.400 | 2/5 | 1.000 | 0.925 | 0.875 | +0.050 | no |
| `piano_or_keyboard_visible` | original | 40 | 0.250 | 1/4 | 1.000 | 0.925 | 0.900 | +0.025 | no |
| `piano_or_keyboard_visible` | negative_example | 40 | 0.250 | 1/4 | 1.000 | 0.925 | 0.900 | +0.025 | no |
| `hands_visible` | original | 40 | 0.143 | 1/7 | 1.000 | 0.850 | 0.825 | +0.025 | no |
| `hands_visible` | negative_example | 40 | 0.000 | 0/7 | 1.000 | 0.825 | 0.825 | +0.000 | no |
| `face_visible` | original | 40 | 0.857 | 24/28 | 0.750 | 0.825 | 0.700 | +0.125 | **yes** |
| `face_visible` | negative_example | 40 | 0.821 | 23/28 | 1.000 | 0.875 | 0.700 | +0.175 | **yes** |
| `sheet_music_visible` | original | 40 | 0.103 | 3/29 | 1.000 | 0.350 | 0.725 | -0.375 | no |
| `sheet_music_visible` | negative_example | 40 | 0.690 | 20/29 | 1.000 | 0.775 | 0.725 | +0.050 | no |
| `text_overlay_present` | original | 40 | 0.909 | 10/11 | 1.000 | 0.975 | 0.725 | +0.250 | **yes** |
| `text_overlay_present` | negative_example | 40 | 1.000 | 11/11 | 0.862 | 0.900 | 0.725 | +0.175 | **yes** |

| field | arm | answered/labelled | declined | acc | baseline | lift |
|---|---|---|---|---|---|---|
| `shot_distance` | original | 39/39 | 0 | 0.462 | 0.513 | -0.051 |
| `shot_distance` | negative_example | 39/39 | 0 | 0.615 | 0.513 | +0.103 |
| `setting` | original | 22/30 | 8 | 0.500 | 0.700 | -0.200 |
| `setting` | negative_example | 29/30 | 1 | 0.276 | 0.700 | -0.424 |

### Candidate status after this round

| candidate | status |
|---|---|
| Gemma 3 4B, production prompt | **measured.** Two fields clear the bar; the rest withheld |
| Gemma 3 4B, negative-example prompt | **measured.** Fixes sheet music decisively, costs two other fields; not adopted |
| Deterministic geometric detectors | **measured and failed** (see above) |
| Qwen2.5-VL 7B Q4 | **blocked on this hardware.** Phase 0B measured it at 7634 MiB; the desktop floor measured 2026-10-27 with llama-server stopped is 1126 MiB, leaving **6864 MiB free** — 770 MiB short. A reduced-context or Q3 build is untested and is the only way it could fit |
| One cloud provider, own media only | **not run.** `cloud_vision` is capped at $0.00 and the owner has authorised Apify only. Competitor frames could not go to a cloud provider in any case without first checking that provider's terms and the source's, which has not been done |

### Reproducing this

```
python scripts/provision/m3_prompt_arms.py --out arms_own.json
# competitor half, only while its transient_media rows are live:
#   on the server:  python m3_competitor_frames.py
#   then locally:   --frames-file <that file> --labels <competitor fixture>
python scripts/provision/score_m3_arms.py \
    --half own        --arms arms_own.json  --labels tests/fixtures/vision/holdout_labels_own.json \
    --half competitor --arms arms_comp.json --labels tests/fixtures/vision/holdout_labels_competitor.json
```

Every figure in this addendum was emitted by that scorer and checked
back against its JSON. None was computed by hand.

**The predictions are committed, so this is reproducible.** The raw
model output for both arms now lives beside the labels, in
`tests/fixtures/vision/holdout_predictions_own.json` and
`holdout_predictions_competitor.json`. Until it did, every figure here
rested on a file in a temporary directory: the labels were in the
repository and the predictions were not, so nothing could be rechecked
and nothing could be re-scored. `tests/server/test_vision_promotion.py`
rebuilds the confusion matrix for every promoted field from those
fixtures, longhand, and fails if the registry, the published figures
and the raw output ever disagree. The fixtures contain model answers
and token counts only — no frames, no bytes.

### These 80 labels are now spent

They chose a prompt and promoted a field. That is selection, and a set
used to select can no longer measure generalisation of what it
selected — which is exactly what happened to the 24 tuning labels,
twice, and is why this set had to be built in the first place.

The benchmark above stands as published and is not revised. What it
may not do is serve as the holdout for the *next* decision. **M4 and
anything after it need a new prospective holdout, labelled before the
candidate's output exists.** Both fixtures record this under
`_spent_by` and a test asserts the record is there, so the constraint
travels with the data rather than living only in this paragraph.
