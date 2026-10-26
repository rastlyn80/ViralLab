# Hook extraction: what was validated, and what failed first

Phase 6A. Every number here was measured on the real corpus — 258 preserved
Instagram videos from the owner's own account — not projected.

Written 2026-10-26, extractor `hook-deterministic-v1`, speech
`hook-speech-v2`.

---

## 1. The corpus is much smaller than it looks

| population | n |
|---|---|
| Instagram publications | 385 |
| with preserved media | **258** |
| with a retention metric (`reels_skip_rate`) | **49** |
| with **both** media and retention | **48** |

The analysis population is **48**, not 258. That is the single most
important number in this phase, and it is a hard ceiling: retention metrics
come from the Instagram insights the collector captured, and they do not
exist for the other 210 preserved videos.

**`ig_reels_avg_watch_time` is not a completion fraction.** It includes
replays. One real video in this corpus (`18337027147171145`, 30.6 s)
reports an average watch time of 103.7 s — **3.38× its own duration**.
Dividing it by duration gives "average loops", not "percentage watched",
and nothing in this phase treats it as the latter. `reels_skip_rate` is the
outcome used throughout: bounded 0–100, about early abandonment, which is
what a hook can plausibly move.

---

## 2. Two detectors were wrong, and one was deleted

### The speech heuristic: 12 right answers, 12 wrong ones

v0 of the extractor shipped a hand-built speech detector combining spectral
flatness variance, zero-crossing-rate variance and a 300–3400 Hz band
ratio. Validated against Silero VAD and Whisper on 12 real openings:

| | heuristic | truth |
|---|---|---|
| speech present | **12 / 12** | **0 / 12** |

It was not mis-tuned. Sustained solo piano varies those statistics exactly
as much as speech does, so the three discriminators carry no signal for
this niche. **The heuristic was deleted rather than tuned**, and the
statistics it was built from are now stored as descriptive values that are
not allowed to decide anything.

Had it shipped, it would have opened the ASR gate on every video and
written 12 hallucinated transcripts into the database as observations.

### Whisper on 3-second clips: 100% hallucination

`[PRIMARY, measured 0B and reproduced here]` Whisper invents text on
music-only audio. On the first pass over the 3-second hook window alone:

| transcript | reality |
|---|---|
| `'you'` ×2, `'You'` | piano, no speech |
| `'Всем уход!'` | piano, no speech |
| `'컴퓨터 컴퓨터 컴퓨터 네, 네, 네'` | Italian pianist, `no_speech_prob` **0.089** |

The Korean one is the instructive failure: the model was *confident*. The
cause is not the gate — Whisper identifies language from roughly 30 seconds
of audio, and on 3 seconds over music it guesses.

**Fix (`hook-speech-v2`):** transcribe a **15-second context window**, then
keep only the segments overlapping the first 3 seconds. Plus two more
gates: reject when `language_probability < 0.60`, and reject any token
repeating 3+ times consecutively (the decoder-loop signature that the
Korean transcript showed).

### Result after the fix, on all 48

| | n |
|---|---|
| speech detected | **4 / 48** |
| transcripts rejected as hallucinations | **8** |

All four surviving transcripts were checked against the post's caption,
which is independent evidence written by the creator:

| transcript (first 3 s) | caption |
|---|---|
| "There is a place you cannot go back to." | "There's a place you can't go back to." |
| "guys thank you so much I just reached five thousand" | "I just passed 5K on YouTube…" |
| "Hey, I was actually thinking that sometimes one piece of music is…" | "What's the most emotional piece for you?" |
| "Almost 20,000 subscribers in less than two weeks…" | "To celebrate 20,000 YouTube subscribers…" |

**4 of 4 correct.** The rejected set included `'you'` ×4, `'Thank you.'` ×2,
`'You'` ×2 and Japanese gibberish caught by the language gate.

**Speech is near-absent from this corpus (4/48, 8%).** That is itself the
finding: speech-based hook features have almost no variance here and cannot
explain retention, so no further ASR investment is warranted until that
changes.

---

## 3. The on-screen-text proxy, calibrated by eye

12 openings rendered as a contact sheet and judged visually. **10 of 12
genuinely carried on-screen text** — this creator captions almost
everything.

| score | truth | |
|---|---|---|
| 0.047 | text | small caption, missed at threshold |
| 0.050 | text | small caption, missed at threshold |
| 0.063 | **no text** | dark room, grand piano |
| 0.070–0.222 | text (7 cases) | correctly flagged |
| 0.165 | **no text** | busy studio: window frames, music stand |

No threshold separates them cleanly: a real caption scores 0.047 while a
text-free studio scores 0.165. At the chosen threshold of **0.067**:
precision **0.89**, recall **0.80**, n=12.

**The honest conclusion is that the boolean is nearly worthless here.** With
a base rate around 83%, "always say yes" would score 0.83 accuracy and the
detector does not clearly beat it. The boolean is therefore stored with its
measured error rate attached, and the **continuous** `text_region_score` is
what the analysis actually uses — it carries information about *how much*
strong-stroke content a frame has, which is a different and more useful
quantity than a yes/no.

It is a proxy, not OCR. It cannot read anything, and actual text content
remains `unavailable` pending a model.

---

## 4. Throughput

| pass | per video | 48 videos | projected 258 |
|---|---|---|---|
| deterministic (ffmpeg + numpy) | **0.94 s** | 45.1 s | ~4.0 min |
| speech (faster-whisper small int8, 15 s context) | **3.78 s** | 181.5 s | ~16 min |

**No GPU was used.** Both passes run CPU-only on the authoritative server's
4 cores, where the media already lives — so no video file moved, and the
Windows GPU box was not involved at all. That matters beyond convenience:
the VRAM ceiling measured in Phase 0B (Qwen2.5-VL Q4 at 7634 of 8192 MiB)
means a vision pass cannot share the card with anything, and keeping the
cheap tier off the GPU entirely leaves it free for the Phase 6B work that
genuinely needs it.

Dependencies added: `faster-whisper` on the server. `ffmpeg`, `numpy` and
`PIL` were already present; OpenCV, scipy and librosa were **not** added —
frame decoding, FFT phase correlation, Spearman correlation and
Benjamini-Hochberg are all implemented directly against numpy, because one
correlation does not justify a dependency (CLAUDE.md §6).

---

## 5. What is reliable, and what is not

| feature | method | status |
|---|---|---|
| `cut_count`, `cut_times_s`, `scene_change_count` | deterministic | reliable; hard cuts only, dissolves are missed |
| `mean_abs_frame_diff`, `brightness_mean`, `contrast_rms`, `saturation_mean`, `edge_density` | deterministic | reliable, reproducible to the bit |
| `camera_motion_px_per_frame` | deterministic | reliable for translation; **under-reports zoom and rotation**, which phase correlation does not see |
| `first_sound_at_s`, `audio_starts_immediately`, `audio_rms_*`, `audio_onset_count`, `audio_attack_time_s` | deterministic | reliable |
| `visual_novelty_vs_later`, `audio_novelty_vs_later` | deterministic | reliable; undefined for videos under 6 s |
| `text_region_score` | proxy | informative as a continuous measure |
| `text_present_estimate` | proxy | **weak** — precision 0.89 / recall 0.80 against an 83% base rate |
| `speech_present`, `spoken_words`, `first_speech_at_s` | model | reliable after v2; 4/4 validated against captions |
| `on_screen_text_content` | — | **unavailable**, needs OCR |
| `performer_visible`, `piano_visible`, `hands_visible`, `face_visible`, `framing_class`, `emotional_presentation` | — | **unavailable**, needs a vision model (Phase 6B) |

---

## 6. Needs improvement

1. **`text_present_estimate`** should be replaced by real OCR, not
   re-thresholded. The proxy conflates captions with any
   high-vertical-edge content, and a piano keyboard is exactly that.
2. **`camera_motion_px_per_frame`** sees translation only. A zoom or a
   rotation reads as near-static, which is wrong in a way the current
   figure cannot express.
3. **Dissolves and whip pans** are not counted as cuts and instead inflate
   `mean_abs_frame_diff`, so a softly-edited opening looks like a
   high-motion one.
4. **Novelty is undefined below 6 s** (2 of 48 videos), because there is no
   later window to compare against.
5. **No frame-level visual recognition at all.** Everything about *what is
   in* the opening — performer, hands, piano, framing — is unavailable, and
   that is the largest gap. It is Phase 6B's job.
