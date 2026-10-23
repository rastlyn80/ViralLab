# Phase 0B — Local AI Benchmark Report

**Status: GATE PASSED**, with three design changes required before Phase 1.
Run 2026-10-23. Gate definition: `DEVELOPMENT_PLAN.md` §7 Phase 0B.

All figures below are **measured on the actual hardware**. No cloud AI, no
paid services. Everything installed is free and local.

---

## 0. Hardware correction

| | Plan assumed | Actually present |
|---|---|---|
| GPU | RTX 2070 | **RTX 2070 SUPER**, 8192 MiB, driver 616.56 |
| VRAM idle baseline | not considered | **1517 MiB already in use** by the desktop |

The SUPER is modestly faster than the plain 2070, so published 2070 figures
would have been slightly pessimistic. **The idle baseline matters more than
the model difference** — see §3.

Stack installed: ffmpeg 9.0.2, Python 3.12.10 (the system Python 3.14 has no
usable ML wheels — faster-whisper, PyTorch and friends do not yet publish for
it, so a 3.12 venv is required and that constraint should be recorded),
faster-whisper 1.2.1, OpenCV 5.0.0, librosa 1.0.0, Ollama + `qwen2.5vl:7b`.

Two environment gotchas worth recording for Phase 1: CTranslate2 needs
`cublas64_12.dll`/`cudnn64_9.dll` on the **process PATH** (the pip
`nvidia-*` wheels ship them but `os.add_dll_directory` was not sufficient),
and PyAV 16 removed a kwarg faster-whisper's internal decoder passes, so
audio must be fed in pre-decoded as a float32 array.

---

## 1. Measured throughput

Test corpus: 14 real niche videos, 7.8 s – 196 s, 576×1024 – 720×1280, all
h264. Each stage timed independently.

| Stage | Cold load | Per-video (steady state) | Throughput |
|---|---|---|---|
| **ffmpeg decode** (4 frames + 16 kHz audio) | — | 0.25 – 1.02 s | **82–199× realtime** |
| **faster-whisper `small`** int8 | **1.0–1.2 s** | 0.08 – 2.88 s | **258× realtime** (median; min 31×) |
| **faster-whisper `medium`** int8 | **2.4–17.6 s** | 0.16 – 2.45 s | **102× realtime** (median; min 8.8×) |
| **Qwen2.5-VL 7B** (ollama, Q4) | **~44.7 s** | **2.25 s / frame** (median) | — |
| **OpenCV shot detection** | — | 0.94 – 7.28 s | ~27–68× realtime |
| **librosa** audio features | 1.34 s (numba JIT) | **0.09 s** | **511× realtime** |

Two of these numbers needed correcting mid-phase, and both corrections
matter:

- The **first Whisper run reported 328× RTF with empty transcripts.** That
  was not a throughput measurement — the VAD filter classified the audio as
  non-speech and skipped nearly all the work. Re-measured with
  `vad_filter=False` to force full processing, the honest figure is **258×**
  for `small`. The original number is withdrawn.
- The **librosa 19.2 s first call was numba JIT compilation**, not per-video
  cost. Steady state is 0.09 s — a 200× difference between the first call
  and every subsequent one.

---

## 2. Cold-load time dominates — batching is now mandatory

This was the Phase 0B question flagged as potentially architecture-changing,
and the measurement confirms it decisively.

| | Time |
|---|---|
| VLM cold load | **44.7 s** |
| VLM inference, 4 frames (one Tier 2 video) | **9.0 s** |
| **Ratio** | **5 : 1 against useful work** |

Under a naive one-job-per-video design, Tier 2 would cost ≈ 56 s/video of
which 80% is loading a model to answer four questions. Batched by modality —
lease a batch, run all transcription, unload, load the VLM, run all vision —
the 46 s of combined cold loads amortises to **0.46 s/video over a 100-video
batch**.

**Decision for the Phase 1 ADR: job granularity is per-modality-batch, not
per-video.** Per-video idempotency keys are retained (each video still gets
exactly one result row per stage), but the lease unit becomes the batch. This
needs settling in the schema before any job table is written, which is
precisely why it was worth measuring first.

---

## 3. GPU memory — the real constraint

| Stage | Peak VRAM | Of 8192 MiB |
|---|---|---|
| Desktop idle (before any work) | 1517 MiB | 19% |
| faster-whisper `small` int8 | 1896–2038 MiB | 25% |
| **Qwen2.5-VL 7B Q4** | **7634 MiB** | **93%** |

**Only ~558 MiB of headroom with the VLM resident.** Consequences:

1. **The one-modality-at-a-time rule is not a preference, it is a hard
   requirement.** Whisper (≈2.0 GiB) and the VLM (7.6 GiB) cannot coexist —
   together they exceed the card.
2. **The Windows machine must not run other GPU work during a batch.** The
   7634 MiB peak was measured against a 1517 MiB idle baseline. A browser
   with hardware acceleration, a game, or a video call could push the total
   past 8192 MiB and force CPU offload or an OOM. Phase 1 should check free
   VRAM before leasing a vision batch and defer rather than thrash.
3. The plan's `[SECONDARY]` estimate of **~4.5 GiB for INT4** was **too
   optimistic** — the real figure including the vision tower and KV cache is
   **7.6 GiB**. The model fits, but with far less room than assumed. A
   smaller VLM should be kept on the shortlist if the headroom proves
   fragile in practice.

---

## 4. Qualitative accuracy

### Qwen2.5-VL: passes convincingly

Prompted for structured JSON (shot type, camera angle, subject, on-screen
text, lighting, mood) on sampled frames. Verbatim results:

| Video | Model output (abridged) |
|---|---|
| apidojo_1 | `subject: "Lang Lang playing piano"`, on-screen text captured across multiple lines: *"THE ART OF VIRTUOSITY / LA CAMPANELLA, BUT IT GETS PROGRESSIVELY MORE ANIMATED / 1) LANG LANG (SHOWMANSHIP)…"* |
| ig_14 | `subject: "Jacqueline du Pré playing cello"`, text: *"No One Played the cello like her… J. Brahms Cello Sonata with Daniel Barenboim"* |
| ig_18 | `shot_type: "wide"`, `subject: "Grigory Sokolov playing piano"`, text: *"Bach's Toccata in E minor"* |
| ig_12 | `on_screen_text: "Bmaj7"` — a small chord label |
| apidojo_5 | `subject: "piano playing hands"`, `on_screen_text: null`, `mood: "intimate, focused"` |

It **named three performers correctly** (Lang Lang, Jacqueline du Pré,
Grigory Sokolov), **distinguished cello from piano**, read **multi-line
overlay text** accurately, and correctly returned `null` where no text was
present rather than inventing some. Output was valid JSON in every case
(wrapped in ``` fences, which the parser must strip).

For a 7B model at Q4 on consumer hardware, this is well above the bar the
gate required. **No cloud VLM is needed for this task.**

### faster-whisper: near-useless for this niche, and actively harmful

**Non-trivial transcript on 1 of 14 videos (`small`), 2 of 14 (`medium`).**
The rest are music-only. What Whisper emits instead of nothing:

```
"Thank you."              "You"                "Music"
"🎵 Piano Music 🎵"       "Ring, ring, ring."   "Oh  Yeah  What  Oh"
"Hello, I am a  Hello, I am a  Hello, I am a  Hello, I am a …"
```

These are **hallucinations on non-speech audio** — a well-known Whisper
failure mode. They are not harmless: fed into caption/transcript text
features or embeddings, "Thank you." appearing on a third of the corpus
would be a spurious signal correlated with nothing but silence.

This answers plan open question 6 (*"Does Whisper degrade unacceptably on
piano-performance audio?"*): **for music-only content there is nothing to
transcribe, and the model fabricates text rather than returning empty.**

---

## 5. Three required design changes

**1. Gate ASR behind speech detection.** Run a cheap speech-presence check
first (VAD, or an energy/spectral-flatness heuristic) and **store `NULL`
when no speech is detected — never the model's output.** Whisper must not be
trusted to return empty on music. Anything shorter than a few words, or
matching the known hallucination set, is discarded.

**2. On-screen text is the primary text channel for this niche, not speech.**
The VLM read overlay text reliably on videos where ASR produced nothing.
Tier 2 priority inverts: vision first, ASR opportunistic. A dedicated OCR
pass may still add value for small/fast-cut text, but it is no longer the
critical path — the VLM already handles the legible cases.

**3. Use Whisper `small`, not `medium`.** `small` is 2.5× faster (258× vs
102× RTF) with a lower cold load, and since the output is near-worthless for
this content there is no case for paying for a larger model. Revisit only if
spoken-word content (talking-head, tutorial) becomes a target.

---

## 6. Revised cost projections — measured

Per-video Tier 2 (4 frames) and Tier 3 (12 frames), steady state, cold loads
amortised over a 100-video batch:

| Component | Tier 2 | Tier 3 |
|---|---|---|
| decode | 0.3 s | 0.6 s |
| whisper `small` | 0.2 s | 0.2 s |
| **VLM** | **9.0 s** | **27.0 s** |
| shot detection | 1.3 s | 1.3 s |
| librosa | 0.1 s | 0.1 s |
| amortised cold load | 0.5 s | 0.5 s |
| **total** | **≈ 11.4 s** | **≈ 29.7 s** |

The VLM is **79% of Tier 2** and **91% of Tier 3**. Frame count is therefore
the single cost lever in the whole pipeline.

### GPU wall-clock, at the plan's 10% Tier 2 / 1% Tier 3 mix

| Corpus | Tier 2 count | Tier 3 count | **Total GPU time** |
|---|---|---|---|
| 1,000 | 100 | 10 | **~24 min** |
| 10,000 | 1,000 | 100 | **~4.0 hrs** |
| 100,000 | 10,000 | 1,000 | **~40 hrs (~1.7 days)** |

Tractable for an occasionally-on machine. Revision 2's projection of
"6–17 days" at 100k assumed full multimodal analysis of every video; Revision
3's "under two days" estimate is now **measured at ~40 hrs**.

### Storage — remeasured across a range, per the owner's correction

The Phase 0A figure came from **one** video and was not a representative
measurement. Across **14 files spanning 7.8 s–196 s, four resolutions and
322–3074 kbps**:

| | Measured |
|---|---|
| MB per second of video | 0.040 – 0.384 (**median 0.079**, mean 0.103) |
| **Spread** | **9.6× max/min** |
| File size | 1.01 – 15.37 MB (median 2.67) |
| Bitrate | 322 – 3074 kbps (median 632) |

| Corpus | Median-based | p90-based (headroom) |
|---|---|---|
| 1,000 | 3 GB | 5 GB |
| 10,000 | 27 GB | 51 GB |
| 100,000 | **267 GB** | **514 GB** |

The 9.6× spread is the point: the single-video figure (0.096 MB/s) happened
to land near the median, but nothing about one sample could have established
that. **Still provisional** — 14 files from one niche, one platform pair, and
encoding profiles change. Treat the p90 column as the planning figure for
provisioning.

Electricity remains negligible: ~40 hrs at ~300 W ≈ 12 kWh for a 100k corpus.

---

## 7. Gate assessment

| Condition | Result |
|---|---|
| Measured **total** Tier 2 cost viable at intended volume | ✅ 11.4 s/video; 40 hrs for a 100k corpus at the tiered mix |
| VLM output quality passes qualitative review | ✅ Named performers, read multi-line overlay text, correct `null` handling |
| All `[ASSUMPTION]` throughput/VRAM figures replaced | ✅ §1, §3, §6 |
| Cold-load vs inference ratio established | ✅ 5:1 → batching decision made (§2) |

**Gate PASSED.**

---

## 8. Outstanding before Phase 1

1. **Qualitative check on the owner's own masters is still missing.** The
   benchmark used niche videos because the folder holding the owner's piano
   content has not been identified. Most video files on this machine are
   private family material and were deliberately not touched. This matters
   because own-content analysis is the point of the system, and the
   repertoire-specific questions (which piece, tempo, framing conventions)
   can only be checked against real own-account footage.
2. **ADR for job granularity** (per-modality-batch) — to be written before
   the Phase 1 schema.
3. Python 3.12 venv requirement to be recorded in Phase 1 setup docs.
4. Free-VRAM precheck before leasing a vision batch (§3).

---

## Appendix — reproduction

```bash
cd probes/local_ai
# storage benchmark across the duration/resolution/bitrate range
python bench_storage.py --urls ../data_access/bench_urls.local.json --n 14
# throughput / VRAM / quality
python bench_gpu.py --media media --whisper-sizes small --frames 4 --limit 6
# corrected whisper measurement (vad off = true compute cost)
python bench_whisper_fix.py
```

Requires `FFMPEG`/`FFPROBE` env vars and the cuBLAS/cuDNN directories on
`PATH` (see §0). Downloaded media and `*_results.json` are gitignored.
