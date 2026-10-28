# ADR 0010 — Public third-party video may be fetched with yt-dlp

**Date:** 2026-10-28
**Status:** Accepted (owner's decision)
**Supersedes in part:** the third-party restriction in ADR 0007 and
the "provider only" position in `server/collectors/competitor_media.py`

## Context

Nothing visual had ever been measured on another creator's video:
**0 of 3,388** publications had been asked a single vision question,
and every competitor finding about how a video *looks* rested on the
218 with any visual measurement at all. The owner's own requirement
names camera angle and overlay explicitly, so this was the largest
hole in the product.

The blocker was not technical and not really money. `yt-dlp` was
already in the codebase, already running with `--no-cookies`, and
already fetching public TikTok video for the owner's own account. The
same call works on anyone's public video. What stopped it was a
position recorded in `competitor_media.py`:

> Fetching a competitor's mp4 directly off TikTok's CDN would mean
> this project doing the access itself. Routing it through the
> scraper's own paid `video-download` add-on means the provider
> delivers the file under terms it has already agreed with the
> platform, and we hold a receipt.

CLAUDE.md §7 then hardened that into "this does **not** extend to
third-party media, and the code cannot be configured to extend to
it."

The owner challenged the resulting cost directly: the tool exists, it
is free, and it was already built. That is correct, and the framing
offered to him — that this was a spending-cap problem — was wrong. It
was a policy choice, and the policy was his to change.

## Decision

`yt-dlp` may fetch **public** third-party video, through one new
module, `server/collectors/competitor_archive.py`, under conditions
that are properties of the code rather than of its configuration:

1. **No credential, ever.** `--no-cookies` on every invocation. The
   module exposes no cookie parameter, so the private path in
   `tiktok_archive.py` is not reachable from it. A caller cannot
   turn this into an authenticated scraper by passing an argument,
   because there is no argument to pass.
2. **Public content only.** A video that does not resolve logged-out
   is recorded as unavailable and skipped. It is never retried with
   credentials and never worked around. This is the line between
   collecting public data and circumventing an access control, and
   DEVELOPMENT_PLAN §2.3 rests on it entirely.
3. **Transient.** Every file is registered in `transient_media` and
   deleted on the ADR 0009 six-hour deadline, whether or not feature
   extraction succeeded. Third-party media is never served to the
   browser and never enters the media root.
4. **Rate limited and bounded.** At least as polite as the
   own-account path, with a per-run ceiling, because this is
   research with no deadline.

The paid provider route stays in place and is not deprecated.

## Consequences

**What improves.** Competitor visual measurement becomes possible at
all, and at full fidelity rather than one frame: framing, movement,
on-screen text and its timing, and audio. It costs nothing, so the
constraint becomes politeness and wall-clock rather than budget.

**What we give up.** The receipt. Under the provider route, the
platform's terms were negotiated by the provider and we held an
invoice proving it. Doing the access ourselves means the project is
the party making the request, and the only defence is that the
content is public and logged-out, the use is internal research, and
nothing is redistributed.

**What this does not license.** Not authenticated access, not
private or region-restricted content, not a redistributable creator
database, and not commercial use. §2.3's legal review before any
commercialisation now has a specific thing to review, and the
provider route exists precisely so that review has somewhere to land
if the answer is no.

**An honest limit on "transient".** As with the provider route, the
six-hour guarantee covers our disk. Here there is no third-party
key-value store holding a second copy for seven days, which is one
respect in which this route retains *less* than the paid one.

## Alternatives considered

**Re-fetch metadata for cover images (~$7).** Rejected as the primary
route: one frame cannot answer camera movement or when text appears.
Worth noting that checking this question found a real bug — the
`clockworks` parser was discarding `videoMeta.coverUrl` on every one
of the 3,388 publications already collected, so the free frame the
provider was already supplying had never been kept. Fixed separately;
future collection keeps it regardless of this ADR.

**Raise the provider download cap (~$6 for ~100 videos).** Keeps the
receipt and covers 3% of the corpus. Still available, and the right
answer if commercialisation becomes real.
