# Local media matching — results

Matching the owner's local archive to published posts.
**No original file was modified, moved or deleted.**

Revised 2026-10-24 after the audio fingerprint was replaced
(`docs/benchmarks/2-audio-fingerprint.md`) and the performance/variant layer
was introduced. The previous revision of this file also contained an
inconsistency — a summary row saying "4 masters, 2–3 posts each" above a
table listing **five** masters — which is corrected here; every figure below
comes from one run of `scripts/match_local_to_posts.py`.

## Match rates (Instagram, 258 publications with preserved video)

| | count | share |
|---|---|---|
| **Auto-linked** (score ≥ 0.80) | **129** | 50% |
| **Queued for review** | **27** | 10% |
| Unmatched | 102 | 40% |
| | | |
| Local masters referenced by a match | 124 of 251 | |
| One master → several publications | **5 masters, 2 publications each** | |

Auto-link quality: score min **0.82**, median **0.99**; audio agreement min
**0.79**, median **0.99**. **All 129** rest on the measured spectral
fingerprint — none on the superseded envelope.

Against the previous run (envelope-era): auto-links **93 → 129**, review
**40 → 27**, unmatched **125 → 102**.

## Validating the 93 earlier automatic links

The earlier links could not be assumed correct, because the audio descriptor
behind them was later measured and found unable to separate a different-hook
cross-post from an unrelated performance. Re-scoring every one of them with
the spectral method:

| | count |
|---|---|
| Still auto-linked, **same master** | **92** |
| Still auto-linked, **different master** | 1 |
| No longer auto-linked | **0** |
| Newly auto-linked (the envelope had missed these) | 36 |

The single change is a **correction, not a false positive**: post
`17969161020009054` pointed at `Liszt.mp4` (score 0.81, audio 0.916) and now
points at `Liszt Longer.mp4` (0.94, audio 0.963) — the same performance, a
better-matching export.

**No contradicted link was found**: there is no case where the measured audio
says "different recording" about a pair the old scorer had accepted.

`[ESTIMATE]` This is **agreement between two automated methods, not measured
precision.** It bounds nothing on its own: both methods share the same frame
hashing, and a pair they both get wrong would appear here as agreement.
Genuine precision needs human confirmation, which is what
`scripts/review_ui.py` and the stratified sample in
`scripts/validate_matches.py` exist to collect. **A similarity score is never
treated as a probability of correctness.**

### The portrait series specifically

The series that produced ~10 confident false links in the first pass is the
hardest case in the archive: visually identical, ~9 s, differing only in the
music. Under the spectral fingerprint, consecutive portraits score
**0.423–0.425** against each other — far below the 0.70 matching floor — while
genuine matches in the same series score 0.99+. No false link from this
series survives.

## What changed the design, twice

### First: audio had to exist at all

The first run auto-linked 89 pairs at a median score of 1.00, and many were
**wrong**. `Britney Spears.mp4` matched the William Blake, Walt Disney, Noam
Chomsky and Andrew Carnegie posts; `Asimov.mp4` matched Tolkien and Newton.
Frame hashing scored them 1.00 because, visually, they *are* the same. A
perceptual video hash cannot solve this; the discriminator had to be audio.

### Second: audio had to become the gate, not a tiebreaker

Adding a loudness envelope cut the false links from ~10 to 1, but the owner
then supplied a fact that changed the requirement: **~99% of videos are
cross-posted, with a deliberately different opening hook and different
on-screen text per platform.** Those edits change pixels. So the normal case
is *the same recording, visibly different edit* — and the envelope scored
exactly that case **0.563–0.781**, below what it scored on true negatives
(up to 0.799).

Two consequences:

1. The envelope was replaced by a spectral fingerprint whose measured gap is
   **+0.437** where the envelope's was **−0.236**.
2. **Audio became the gate and visuals the corroboration.** Audio decides
   *whether* this is the same recording; visuals decide whether it is the
   same *edit*. A strong audio match with a weak visual one is now a probable
   variant to review rather than something to discard — and a strong visual
   match with no audio support is still never auto-linked.

Of the 129 auto-links, **3 carry a nonzero audio offset**, meaning the
publication starts later than the master: a trimmed head or a different hook,
detected rather than penalised.

## Performances and variants

A publication was often scoring ~1.0 against *three* different local files,
so the matcher called it ambiguous and asked the owner to choose. That was
the wrong question. The files were **variants of one performance** — different
exports of the same take:

- `Billie Eilish - Improved` / `- with Intro` / `- Test 3` …
- `Walt Disney` / `Walt Disney 1` / `Walt Disney 2`
- `Pop Etude 1` / `- Manychat` / **`- TikTok`** ← named for its platform

`scripts/group_performances.py` groups masters by audio (never by filename;
filenames only *label* a group). Result: **156 performances across 250
masters**, 63 with more than one variant, largest group 7.

The matcher now distinguishes the two kinds of near-tie:

- **same performance** → the match is confident, only the export is open.
  Linked, with `variant_ambiguous` recorded so the chosen filename is never
  mistaken for an established fact. **47 of the 129 auto-links are in this
  state.**
- **different performances** → genuinely ambiguous, and still reviewed.

This is why review dropped from 88 (mid-development) to 27 without lowering
any threshold.

### A defect this exposed

Grouping first produced a 33-member "performance" containing Frank Zappa,
Fur Elise, Chopin Concerto and Britney Spears. Cause: the comparison scored
the matched fraction *of the aligned span*, which rewarded finding a tiny
agreeable window. `Billie Eilish.mp4` and `Unicef.mp4` — unrelated, 517 and
560 frames — scored **1.000 on 22 aligned frames**, 4% of either, and
single-linkage chaining did the rest.

Fixed by requiring the aligned span to cover at least half the shorter
fingerprint and at least ~2 s. Measured against the shorter side on purpose,
so a 15 s Reel cut from a 60 s master still covers 100% of the Reel and
genuine trims keep matching. The five spurious pairs fell to **0.00–0.20**
while genuine variants held **0.88–1.00**, the largest group fell from 33 to
7, and the benchmark's negatives improved (median 0.294 → 0.134) with **no
positive affected**.

Groups also now report **cohesion** — the weakest internal link — so a group
held together by one marginal edge is visible as such. One group is flagged
below 0.80.

## Multi-post masters

Five masters legitimately map to more than one publication, which is the
repost / re-edit case the sequence matcher exists to find:

| master | publications |
|---|---|
| Pop Etude Manychat.mp4 | 2 |
| Piano therapy.mp4 | 2 |
| Beethoven born.mp4 | 2 |
| We are the Rach.mp4 | 2 |
| Verde Luce.mp4 | 2 |

## Why 102 publications are unmatched

Not a failure of the matcher so much as a fact about the archive:

- publications predating the files kept in `D:\Videos` / `D:\Portraits\Exports`
- content filmed and edited on a phone, never exported to the workstation
- masters since deleted or archived elsewhere
- the 28 Instagram publications whose media the API withholds
  (`docs/benchmarks/2-instagram-media-coverage.md`)

## Cost

| operation | cost |
|---|---|
| Screening sweep, 251 × 258 = 64,758 pairs | ~1.5 min |
| Full-fidelity re-scoring | **1,039 pairs (1.6%)** |
| Whole run | ~4 min |

A full-fidelity comparison of every pair would have taken hours — the worst
pairs cost ~1.6 s each — so stage 1 screens on a 30 s window at a single
sub-frame phase and stage 2 decides at full length. The screen is a **union**
of two independent cheap signals rather than a veto by either, because a Reel
cut from the middle of a long master may share nothing in its first 30 s and
would be lost to an audio-only screen.

## Storage

| | |
|---|---|
| Instagram media preserved on the server | **258 videos, 736 MB** |
| Local short-form candidates | 257 files, 13.21 GB |
| Masters referenced by a match | **124** |
| Estimated transfer if only matched masters move | ~6 GB |

Local masters have **not** been transferred. Their fingerprints — audio
included — are now stored in the database, which is what lets the **server**
match newly collected publications without this machine being awake. A
transfer decision can wait until there is a reason to need the pixels.

## TikTok

Covered separately in `docs/benchmarks/2-cross-platform-matching.md`. In
short: TikTok returns no media file, the cover image was implemented and then
**measured and rejected** as a matching signal, and cross-platform pairs are
therefore proposed from metadata for human confirmation — never auto-linked.

## Review workflow

`python scripts/review_ui.py` → http://127.0.0.1:8770

Side-by-side playback of the local master and the published clip, the full
evidence behind the proposal, and four answers rather than two: yes, no,
**skip**, and **"same performance, different edit"** — which records a real
relationship that previously had nowhere to go. Every decision is written
immediately, attributed to `human` with a timestamp, so the queue resumes
exactly where it was and **reviewing is never a prerequisite for other work**.

Automatic re-runs never overwrite a human decision, and prune only links the
machine itself created.
