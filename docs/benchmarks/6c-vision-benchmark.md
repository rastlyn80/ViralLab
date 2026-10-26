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
