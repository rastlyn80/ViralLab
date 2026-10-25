# Multimodal research pipeline — design

How a discovered competitor publication eventually becomes structured,
searchable characteristics. **Nothing here runs a large-scale analysis yet**;
this document plus the job seam in `server/research/analysis_seam.py` is the
deliverable, so Phase 6 can be built without redesigning Phase 4.

Written 2026-10-25, against migration head `d9f4a2b76c81`.

---

## 0. The permission question comes first

**Public metadata access is not permission to download and retain media.**
That distinction governs everything below and is easy to lose, because the
validated providers *do* return a direct media URL — `[PRIMARY, measured 0A]`
`apidojo` returns `video.url` and both Instagram actors return `videoUrl`,
and bytes were actually fetched from one during Phase 0A.

Being able to is not being allowed to. The operating policy
(`DEVELOPMENT_PLAN.md` §2.3) permits public, logged-out data for **internal
research only**, with no redistributable creator database. So:

| | own content | competitor content |
|---|---|---|
| metadata | retained indefinitely | retained indefinitely |
| derived features | retained indefinitely | retained indefinitely |
| **media bytes** | **retained** (`media/`, 258 files) | **transient only** |

**Competitor media is fetched, analysed, and deleted** — never accumulated
into a library. The features survive; the video does not. Concretely:

- a retention ceiling measured in hours, enforced by the job, not by habit;
- the file is deleted once features are persisted, in the same place the
  existing worker already deletes its copy after the server confirms the
  result (CLAUDE.md §4.7);
- `video.media_status` for a competitor publication stays `absent`, because
  it genuinely is;
- no competitor media is ever served through the API, not even signed.

The last two were stated here before anything enforced them, and one of the
collectors was in fact violating the first. §6 records where each is now
actually checked, and which test holds it there.

`[OPEN]` Whether even transient download is appropriate for every source is
a question for legal review before commercialisation, which §2.3 already
requires. Until then this stays internal-research-only and the volume stays
small — which the sampling design below enforces anyway.

---

## 1. Shape of the pipeline

```
 discovered publication (videos row, no media)
        │
        ▼
 [1] acquisition        transient fetch, validate, hash, TTL-bounded
        │
        ▼
 [2] frame + audio      ffmpeg: N frames, mono 11025 Hz audio
     extraction         (the existing probes/local_library code path)
        │
        ├──► [3] musical      repertoire, tempo, dynamics, climax
        ├──► [4] hook         first three seconds, specifically
        ├──► [5] language     ASR (speech-gated) + VLM-read on-screen text
        ├──► [6] visual       angle, framing, lighting, setting
        └──► [7] editing      cuts, transitions, pacing, duration
        │
        ▼
 [8] persist            video_features rows: value + method + model +
                        version + confidence + extracted_at
        │
        ▼
 [9] human review       corrections stored ALONGSIDE, never over
        │
        ▼
 [10] delete media      features kept, bytes gone
```

Each numbered stage is a separate job type on the existing Postgres queue.
That matters because of a measured constraint: `[PRIMARY, measured 0B]`
Qwen2.5-VL Q4 peaks at **7634 MiB of 8192**, so the VLM and Whisper
**cannot be co-resident**, and VLM cold load is **44.7 s against 9.0 s of
inference**. Hence ADR 0002: batch by **modality**, never by video. Stage 3
runs across a hundred videos, then unloads, then stage 5 runs across the
same hundred.

---

## 2. What gets extracted

The vocabulary below is the `feature_key` namespace. Every row carries
method, model, version and confidence regardless of which extractor
produced it (`docs/SCHEMA_PLAN.md` §4).

### Musical

| feature_key | type | method | notes |
|---|---|---|---|
| `music.provider_title` | text | provider | **already collected** — `song.title` / `musicMeta.musicName` / `musicInfo.song_name` |
| `music.provider_artist` | text | provider | already collected |
| `music.is_original_audio` | bool | provider | already collected on IG and clockworks |
| `music.work` | text | human + match | the actual piece, e.g. "Ballade No. 1 in G minor" |
| `music.composer` | text | human + match | |
| `music.genre` | enum | VLM/heuristic | classical, neoclassical, jazz, pop-cover |
| `music.recognisable_passage` | bool | audio match | does it open on a famous bar |
| `music.tempo_bpm` | number | classical DSP | onset/beat tracking |
| `music.dynamic_range_db` | number | classical DSP | cheap and genuinely informative for piano |
| `music.virtuosity` | ordinal | VLM + DSP | note density, hand independence |
| `music.emotional_character` | enum | VLM | melancholic, triumphant, tender, turbulent |
| `music.climax_position_pct` | number | classical DSP | where the loudest sustained passage sits |
| `music.originality` | enum | human + match | repertoire / arrangement / original composition |
| `music.structure` | text | DSP + human | e.g. ABA, through-composed |

**Repertoire identification is the hard one.** The honest approach reuses
what already works: `shared/audiofp.py` matched the owner's own catalogue at
a measured +0.437 separation. The same fingerprint can match a competitor
clip against a reference set of the owner's known repertoire — strong where
it hits, silent where it does not. It cannot name a piece it has never
heard, and `[OPEN]` a general-purpose classical-repertoire identifier is an
unsolved problem we are not going to solve; the fallback is the provider's
music title plus human confirmation.

### Hook — the first three seconds

Treated as its own stage, not a slice of "visual", because it is the single
most actionable thing the product can report and because
`reels_skip_rate` — already collected, `[PRIMARY]` verified live at 57.7% on
one own-account Reel — measures exactly this: "the percentage of views from
people who skipped during the first 3 seconds".

| feature_key | type | method |
|---|---|---|
| `hook.type` | enum | VLM — question, claim, immediate-play, silence, text-first |
| `hook.first_frame_subject` | enum | VLM — face, hands, instrument, text, environment |
| `hook.text_present` | bool | VLM OCR |
| `hook.text` | text | VLM OCR |
| `hook.speech_present` | bool | VAD |
| `hook.music_starts_at_s` | number | DSP |
| `hook.cut_count_first_3s` | int | scene detection |

### Language and publishing

| feature_key | method | notes |
|---|---|---|
| `speech.transcript` | ASR | **speech-gated**: `[PRIMARY, measured 0B]` Whisper *hallucinates* on music-only audio ("Thank you.", "Music") rather than returning empty, so NULL is stored when VAD finds no speech |
| `text.on_screen` | VLM | for this niche the **primary** text channel, not speech |
| `caption.*`, `hashtags`, `posting_time` | already collected | metadata, no extraction needed |
| `cta.present` / `cta.type` | VLM | follow, comment, link, none |

### Visual and editing

| feature_key | method |
|---|---|
| `visual.camera_angle`, `.shot_distance`, `.performer_visible`, `.hands_vs_face`, `.setting`, `.background`, `.lighting`, `.colour_palette`, `.atmosphere` | VLM over sampled frames |
| `edit.duration_s`, `.cut_count`, `.cuts_per_minute`, `.transition_types`, `.has_overlays`, `.has_subtitles`, `.one_take` | scene detection + VLM |
| `edit.pacing` | derived from cut density and duration |

---

## 3. Provenance is mandatory, and an AI label is not ground truth

Every `video_features` row carries `method`, `model`, `model_version`,
`confidence`, `extracted_at`. Four rules:

1. **Re-extraction adds a row.** A new model version never mutates an old
   value; the old row gets `superseded_by`. Model drift has to be visible in
   the data, not hidden by it.
2. **Low confidence is excluded from modelling**, not averaged in.
3. **Corrections live beside the machine value**, in
   `feature_corrections`, never over it. The correction is the truth for
   analysis *and* labelled evaluation data for the extractor.
4. **Absence is recorded as absence.** The ASR gate is the template: no
   speech means NULL, not an empty string and certainly not a hallucinated
   sentence.

---

## 4. The job seam, which exists now

`server/research/analysis_seam.py` is the handover point. It can:

- select publications eligible for analysis, by a **stratified** sampler
  that deliberately includes over-performing, typical and under-performing
  publications (CLAUDE.md §3.12 — a corpus of hits cannot identify what
  distinguishes a hit);
- describe what each stage would cost and require;
- enqueue a batch onto the existing `analysis_batches` queue, which already
  provides leases, heartbeats, bounded retries and reaping;
- refuse, loudly, when media retention policy or GPU memory gating would be
  violated.

It is deliberately *not* wired to a scheduler. Phase 6 turns it on.

---

## 5. Cost, from measured figures

`[PRIMARY, measured 0A/0B]`

| item | measured |
|---|---|
| media download | 0.096 MB/s of video → ~3 MB for a 30 s clip |
| VLM cold load | 44.7 s, once per batch |
| VLM inference | 2.25 s/frame warm |
| Whisper `small` int8 | 258× realtime |
| peak VRAM, VLM Q4 | 7634 / 8192 MiB |

For 1,000 competitor publications at 4 frames each: roughly 2.5 GPU-hours
for vision plus minutes for audio, and ~3 GB of transient media that is
deleted as it goes. That is a Phase 6 decision to take deliberately, with
the tier budget of §5.2 applied — Tier 1 on everything, Tier 2 on a
stratified ~10%, Tier 3 on ~1%.

---

## 6. Where the retention policy is enforced

Three places, because they are three different risks.

| layer | what it stops |
|---|---|
| `server/collectors/media_preservation.own_instagram()` | a competitor's reel being **downloaded and kept** by the unattended pass |
| `server/research/analysis_seam.py` | analysis being enqueued at all where policy or GPU gating would be violated; declares competitor media transient (`COMPETITOR_MEDIA_TTL`, 6 hours) |
| `server/api/ui._signed_media_url()` | a competitor's media being **served out of our own API**, even if a file somehow existed |

The first and third were added after a found defect. `media_preservation`
predates this policy and selected every Instagram publication by platform
alone — correct only while the owner was the sole creator in `videos`, which
Phase 4 silently stopped being true. Nothing connected the two, and the type
system could not catch it.

Scoping is therefore load-bearing policy enforcement rather than tidiness,
and `tests/server/test_media_preservation.py` holds it there — including the
case where a file *named* after a competitor publication must be removed as
an orphan rather than adopted, so the policy cannot be defeated by a
filename.

---

## 7. Open questions

1. **General repertoire identification.** Unsolved; the fallback is provider
   metadata plus human confirmation. → §2 above.
2. **Does a VLM produce *useful* musical judgements**, or confident vagueness?
   Phase 0B's lesson was that speed is not usefulness. A qualitative gate on
   a labelled sample is required before any scale run.
3. **Transient-download appropriateness per source**, for legal review
   before commercialisation.
4. **Hook features vs `reels_skip_rate`.** The one place where an extracted
   feature can be validated against an independently measured outcome, on
   own-account data where both exist. That is the first thing to test, and
   it is a genuine opportunity rather than a caveat.
