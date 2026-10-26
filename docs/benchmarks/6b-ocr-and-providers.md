# Phase 6B: OCR accuracy, provider capabilities, and what the proxy was

Measured on the real corpus — the owner's 258 preserved Instagram videos,
48 of which have retention metrics. Written 2026-10-26.

---

## 1. Local OCR, benchmarked against frames a human read

Ground truth was transcribed by eye from a rendered contact sheet **before
the OCR module was written**, so it is independent of the engine. Two of the
twelve openings genuinely carry no text and are included deliberately: a
dark grand-piano frame and a busy studio — the two cases the old
edge-density proxy got wrong.

| | result |
|---|---|
| **Presence precision** | **1.00** |
| **Presence recall** | **1.00** |
| **Presence accuracy** | **1.00** (10 with text, 2 without, all correct) |
| **Transcription word-recall** | **0.988** mean (min 0.89, max 1.00) over 9 scorable captions |
| Throughput | 3.16 s/video (8 frames each) |

Engine: RapidOCR on onnxruntime. Chosen because onnxruntime was already
present as a faster-whisper dependency, the models are tens of MB, and it
needs no torch — so the GPU stays free.

### What the benchmark exposed about the benchmark

On `1792807609` the human label said "text too small to transcribe" and
**OCR read a full sentence**: *"It feels like desperate sobbing and pleading
a listener after hearing The Last Goodbye with the fear of loss."* The human
ground truth was limited by the contact sheet's resolution, not by the
frame. That video is scored for presence only.

### Two defects found and fixed during the benchmark

1. **Reading order.** Elements were sorted by first appearance, which
   rendered a two-line caption as *"in my next album? Shall I include
   this"* — every word correct, the sentence wrong. Now sorted
   top-to-bottom then left-to-right.
2. **Sheet music is read as text.** `1811189353` produced `"crese."` from a
   *cresc.* marking in the score. This is not fixable at the OCR layer: OCR
   cannot distinguish a caption the creator added from printed text already
   in the scene. The limitation is declared in the provider's capabilities,
   and separating the two is a job for the vision model.

---

## 2. What the corpus actually contains

OCR over all 48 videos with retention metrics: **528 features, 3.77 s/video,
0 failures.**

| | |
|---|---|
| On-screen text present | **46 of 48 (96%)** |
| Word count | min 0, median 9, max 23 |
| Caption changes mid-hook | 12 of 48 |
| Text starts at 0.0 s | 44 of the 46 |

A 96% base rate means `ocr_text_present` has almost no variance, so it
cannot explain variation in anything. The continuous measures do vary and
were tested.

---

## 3. The important finding: the proxy was never measuring text

Phase 6A found `text_region_score` associated with skip rate (ρ −0.563,
p_adj 0.0005) and interpreted it as a text signal, because that is what an
edge-density-in-a-horizontal-band statistic was built to approximate. With
real OCR the interpretation can be tested directly.

**Correlation between the proxy and each real OCR measurement:**

| OCR measurement | correlation with the proxy |
|---|---|
| `ocr_word_count` | **0.01** |
| `ocr_element_count` | **0.06** |
| `ocr_text_changes` | −0.27 |
| `ocr_text_duration_s` | 0.36 |

**Proxy-versus-skip after controlling for each:**

| controlling for | ρ |
|---|---|
| nothing | −0.563 |
| `ocr_word_count` | −0.563 |
| `ocr_element_count` | −0.561 |
| `ocr_text_changes` | −0.564 |
| `ocr_text_duration_s` | −0.517 |

Controlling for the actual amount of text changes essentially nothing.
**`text_region_score` is not a text measurement.** Whatever it detects —
dense, high-contrast vertical strokes — is something else, and that
something is what tracks retention here. Candidates include the keyboard
itself, printed sheet music, and general visual density of the opening
frame. Distinguishing them needs visual recognition, which this phase
declares but cannot run.

**No OCR measurement relates to skip rate.** All six tested: ρ from −0.276
to +0.113, none surviving FDR correction (best adjusted p = 0.39). So the
amount, size, duration and timing of on-screen text do not explain retention
in this corpus.

The stored feature keeps its misleading name, because renaming it would
break comparison with the Phase 6A evidence. Its stored note records what it
actually measures.

### Repeated performances

48 publications collapse to **46 independent units** once takes of the same
performance are grouped (performance 167 contributes 3 videos, 180
contributes 2). The association is unchanged: ρ −0.563 → **−0.574**, so
repeated takes are not driving it.

---

## 4. Provider capabilities, verified

`[PRIMARY]` Read from each vendor's own documentation on **2026-10-26**.
These are not interchangeable, and the registry exists to say so.

| | video input | max images/request | structured output | notable documented limit |
|---|---|---|---|---|
| **local-ocr** (RapidOCR) | frames | — | yes | reads text only; cannot describe a scene |
| **local-vlm** (Qwen2.5-VL Q4) | frames | hardware-bound | no | 7634 of 8192 MiB VRAM; cannot share the card |
| **OpenAI** | **no** | 1,500 | yes | 512 MB payload; 30,000 patches/image |
| **Gemini** | **yes, natively** | — | yes | inline <100 MB; sampled at 1 FPS by default |
| **Claude** | **no** (first frame of an animation) | 600, or 100 at 200k context | no | **will not identify people**; 10 MB/image; approximate coordinates and counts |

Gemini accepting video natively is a genuine capability difference, not a
detail. Claude refusing to identify people is exactly compatible with the
task as written — "is a performer visible" is answerable, "who" is not asked
of any provider.

### Cost per analysed video, estimated

For the actual task — 6 frames (4 across the hook plus 5 s and 12 s) at
540 px:

| provider | estimated | basis |
|---|---|---|
| **gemini** | **$0.0037** | `[ASSUMPTION]` token prices not read from a pricing page |
| **openai** | **$0.0077** | `[ASSUMPTION]` same |
| **anthropic** | **$0.0161** | `[PRIMARY]` visual tokens computed from the published ⌈w/28⌉×⌈h/28⌉ formula at Haiku 4.5's documented $1/Mtok input |
| **local-ocr** / **local-vlm** | **$0.00** | runs on owned hardware |

Only the Anthropic figure is computed from a published formula; the other
two are estimated from payload size and labelled `[ASSUMPTION]`. Analysing
all 48 videos would cost roughly **$0.18–$0.77** depending on provider.

---

## 5. What was NOT measured, and why

**No visual recognition accuracy figure exists**, because no visual model
ran. torch, transformers and the model weights are absent on both machines,
the 8 GB card is on the Windows box while the media is on the server, and no
cloud credential is present. The adapters, the task schema, the routing and
the budget controls are all implemented and tested; the inference is not.

Reporting an accuracy number here would require either installing a
multi-gigabyte stack or spending money, and the brief authorised neither.
`POST /api/ai/analyse` returns **501 with that explanation** rather than an
empty analysis.

**No cross-provider agreement figure exists** for the same reason. The
`compare` routing mode is implemented and tested, and excludes paid
providers unless escalation is separately authorised.

---

## 6. Throughput summary

| pass | per video | 48 videos | GPU |
|---|---|---|---|
| deterministic (`hook-deterministic-v1`) | 0.94 s | 45 s | none |
| speech (`hook-speech-v2`) | 3.78 s | 182 s | none |
| OCR (`hook-ocr-v1`) | 3.77 s | 181 s | none |

**Total GPU time across Phases 6A and 6B: zero.** Every pass runs CPU-only
on the server where the media lives, which is why no media file has moved
and why the card remains free for the vision work that genuinely needs it.
