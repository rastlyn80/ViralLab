# Local video library — feasibility and inventory

Read-only scan of the owner's archive, 2026-10-24. **No file was modified,
moved, renamed or deleted**, and nothing has been transferred yet.

Reproduce: `python scripts/scan_local_library.py --inventory`

## Inventory

| | |
|---|---|
| Files with a video extension | 291 |
| Probed successfully | **291** (0 unreadable, 0 audio-only) |
| Total size | **50.8 GB** |
| Duration min / median / max | 2 s / 32 s / 3414 s (57 min) |
| Exact duplicates | 7 extra copies across 7 groups |

| Root | Files | Size |
|---|---|---|
| `D:\Videos` | 111 | 44.0 GB |
| `D:\Portraits\Exports` | 180 | 6.8 GB |

## The number that matters

| | |
|---|---|
| **Short-form candidates (≤ 180 s)** | **257 files** |
| **Their total size** | **13.21 GB** |

Only these plausibly correspond to published posts. The remaining 34 files
carry **37.6 GB** — long-form material (up to 57 minutes) that cannot be a
TikTok or Reels source and should never be transferred.

**So the transfer is 13.2 GB, not 50.8 GB**, and even that is an upper bound
before matching narrows it further.

## Shape confirms these are the right files

- **260 of 291 are vertical** (height > width) — short-form shape.
- Dominant resolution **1080×1920** (246 files), the standard short-form
  master.
- 257 candidates against **234 published TikTok videos** is a plausible
  near-one-to-one correspondence with room for unpublished takes.

## Matching approach

Three signals, cheapest first:

1. **Duration** — nearly free, and a strong filter. A published post is
   usually within a second of its master.
2. **Perceptual hash of sampled frames** — survives re-encoding, resolution
   changes and moderate cropping, which is exactly what a platform does to an
   upload.
3. **A sequence of frame hashes, not one** — so an edited variant sharing
   only part of its footage still matches on the overlap.

That third point is what supports the two requirements that a single hash
could not: **reposts and edited variants**, and **one source video linked to
several published posts**.

### Why this needs platform media

Matching a local master to a published post requires both sides. TikTok's
Display API returns **no media at all** (ADR 0001), so TikTok-side frames
must come either from the cover image (weak — one frame) or from the
third-party provider route, which carries the §2.3 operating policy.
Instagram **does** return a `media_url`, so Reels can be matched properly.

This is a real limitation rather than an implementation gap, and it shapes
what the matcher can honestly claim for TikTok.

## Uncertain matches

Matching produces a confidence score, not a verdict. The plan is a single
reviewable list — proposed pairs above a high threshold auto-linked, a middle
band queued for one-key confirmation, and low scores discarded. **No manual
organisation of hundreds of files**: the owner only ever sees the ambiguous
middle, which on these numbers should be a small fraction of 257.

## Transfer plan

Nothing moves until matching has narrowed the set. When it does: only matched
short-form files, over the existing TLS channel to the authoritative server,
with Ubuntu remaining the system of record. Originals stay exactly where they
are.
