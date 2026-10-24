# Local media matching — results

Matching the owner's local archive to published posts, 2026-10-24.
**No original file was modified, moved or deleted.**

## Match rates (Instagram, 258 posts with preserved media)

| | count | share |
|---|---|---|
| **Auto-linked** (score ≥ 0.80) | **93** | 36% |
| **Queued for review** | **40** | 16% |
| Unmatched | 125 | 48% |
| | | |
| Local masters used | 125 of 251 | |
| One master → several posts | 4 masters, 2–3 posts each | |

Auto-link quality: score median **0.98**, audio-similarity median **0.96**.

## The finding that changed the design

The first matching run auto-linked **89 pairs at a median score of 1.00** —
and a substantial share of them were **wrong**.

`Britney Spears.mp4` was confidently matched to the William Blake, Walt
Disney, Noam Chomsky and Andrew Carnegie posts. `Asimov.mp4` matched Tolkien
and Newton. The cause is specific and would not have been obvious from the
scores alone: the **"musical portraits" series is visually identical** — same
framing, same piano, same lighting, ~9 seconds each — and differs only in the
music. Frame hashing scored them 1.00 against each other because, visually,
they *are* the same.

A perceptual video hash cannot solve this. The discriminator had to be audio.

**Fix:** a coarse 32-bucket loudness envelope per video, normalised so it
survives re-encoding. Measured on the real files, different portraits score
**0.58–0.64** against each other while true matches score **0.83–1.00**. The
matcher now requires audio corroboration above 0.80; a visual match without
it is capped below the auto-link threshold and sent to review.

**Result: false auto-links from those four masters fell from ~10 to 1** — and
the survivor is `Britney Spears.mp4` → the actual Britney Spears post at
audio 0.91, which is correct.

A second defect surfaced while re-running: **23 stale links from the buggy
first pass survived as `auto`**. Re-matching now prunes any link the current
run no longer supports, while never discarding a human decision.

## Multi-post masters

Four masters legitimately map to more than one post, which is the repost /
re-edit case the sequence matcher exists to find:

| master | posts |
|---|---|
| Chopin Concerto.mp4 | 3 |
| Piano therapy.mp4 | 2 |
| Beethoven born.mp4 | 2 |
| We are the Rach.mp4 | 2 |
| Verde Luce.mp4 | 2 |

## Why 125 posts are unmatched

Not a failure of the matcher so much as a fact about the archive:

- posts predating the files kept in `D:\Videos` / `D:\Portraits\Exports`
- content filmed and edited on a phone, never exported to the workstation
- masters since deleted or archived elsewhere
- the 28 Instagram posts with no downloadable media at all

## Storage

| | |
|---|---|
| Instagram media preserved on the server | **261 files, 736 MB** |
| Local short-form candidates | 257 files, 13.21 GB |
| Masters actually referenced by a match | **125** |
| Estimated transfer if only matched masters move | **~6 GB** |

Local masters have **not** been transferred. Only their fingerprints and
paths are recorded, which is enough for analysis to know what exists. A
transfer decision can wait until there is a reason to need the pixels.

## TikTok

Not yet matched, and the reason is structural: **TikTok's Display API returns
no media** (ADR 0001), so there is no post-side video to fingerprint. The
options are the cover image — a single frame, far weaker than a sequence —
or the third-party provider route under the §2.3 operating policy. This is a
limitation to decide about, not a gap to code around.

## Review workflow

`python scripts/review_matches.py --list | --interactive | --accept-above N`

The owner sees only the 40 ambiguous pairs, each with its score, frame
overlap, duration delta and a `[tie]` flag where the top two candidates are
within 0.08. Decisions are one keystroke and the queue can be abandoned
mid-way. Confirmed and rejected states are never overwritten by a later
automatic run.
