# ADR 0007 — Own-account media archival outside the official API

**Status:** Accepted, 2026-10-26
**Supersedes nothing. Narrows:** CLAUDE.md §7 ("Your own TikTok masters come
from your local archive, not from scraping your own content back off the
platform")

---

## Context

234 of the owner's TikTok publications sit in `videos` with
`media_status='absent'`. TikTok's Display API returns `share_url` and
`embed_link` and **no downloadable file** (`[PRIMARY]`, ADR 0001, re-verified
2026-10-26 against a live `video/list` response). Consequently none of the
hook, OCR, speech or vision pipelines can run on any TikTok publication, and
the account is invisible to every analysis the project exists to perform.

CLAUDE.md §7 anticipated this and answered it with "own masters come from the
local archive". That answer has not held up: `local_masters` holds 251 files
and `video_master_links` 156 confirmed links, against 234 TikTok
publications. The archive is a partial, manually-curated set of performance
masters, not a mirror of what was published — published videos carry
captions, overlays, cuts and edits that the masters do not. For hook
analysis, which is explicitly about the published artefact's first three
seconds, the master is the wrong object even when it exists.

The owner asked for the gap to be closed and stated they are the rights
holder for this material.

## Decision

Implement `server/collectors/tiktok_archive.py`, which fetches the owner's
**own** published videos with `yt-dlp`, under these constraints.

### 1. Ownership is established by the platform, not by configuration

The rejected design was a connector accepting "an array or configuration
object representing the user's verified accounts", with ownership enforced
"at the application layer". A caller-supplied list of owned accounts is a
self-declaration: whatever handle is in the array gets downloaded, so the
check is a comment rather than a control. Combined with arbitrary URL input
it is a general-purpose scraper wearing a compliance label.

Instead, **TikTok's own `video/list` response is the authority**. A video is
fetched only if it appears in the authenticated owner's video list, obtained
with the already-granted `video.list` scope. The platform itself asserts the
video belongs to the authenticated account.

This turned out to be strictly better engineering as well as a stronger
control. `creators.handle` holds the OAuth `open_id`
(`-000lRCwi-AFr6BTkIsmkCkFPfgeI8Izqw5p`), not a username — the scheduler
falls back to `open_id` because `TIKTOK_HANDLE` was never set, and
`user.info.basic` does not return a username (that needs `user.info.profile`,
which this app has not requested). A URL reconstructed from `handle` would
have been invalid for all 234 videos. `share_url` from the same `video/list`
call carries the canonical URL —
`https://www.tiktok.com/@gabrielebaldocci/video/<id>` — so one API call
supplies both the ownership proof and the address, and nothing is
reconstructed.

### 2. Multi-profile by construction, not by configuration

`owned_profiles()` returns every profile with `is_own_account` set, each
tagged with its ownership basis:

| basis | meaning | may fetch public | may fetch private |
|---|---|---|---|
| `oauth` | an `oauth_tokens` row exists for the profile | yes | yes |
| `operator_marked` | `is_own_account` set, no token | no | no |

An agency with fifty owned profiles connects each once; nothing in the
download path is handle-specific. The `handles` argument to
`archive_own_tiktok` **narrows** this set and can never widen it — passing an
unowned handle selects nothing rather than granting permission.

### 3. Public fetches carry no credentials

Public TikTok videos resolve without a session, so the normal backfill runs
`yt-dlp --no-cookies`, with no `--username`, no `--netrc` and no browser
cookie import. There is nothing for the process to leak because it holds
nothing.

### 4. The authenticated path exists, is off by default, and is gated by the
platform

Private, unlisted and region-restricted content needs a logged-in session.
**A TikTok OAuth token cannot supply one**: OAuth tokens authenticate the
Display API, not yt-dlp's player extraction. The only thing that works is a
browser session cookie jar, which is a full-account credential and is
unrelated to — and unverifiable from — the OAuth token. Holding a token for
`@alice` does not prove a supplied cookie jar belongs to `@alice`, so
"OAuth-verified owner" does **not** on its own gate the cookie path.

The gate that does bind:

> The OAuth API is the authority on whether a video is yours.
> The cookie is only transport.

`authorise_private_sync()` requires all of: `VIRALLAB_TIKTOK_PRIVATE_SYNC=1`
in the server environment; an `oauth` (not `operator_marked`) profile; a
token not flagged `needs_reauth`; and **the target video id present in that
profile's `video/list` response**. A video TikTok does not list is refused
however many credentials are offered.

Cookies are supplied per sync, written to a `0600` temporary file, passed by
path, overwritten and deleted in a `finally`. They are never stored in the
database, never written to a log (only their byte count is logged), and never
returned by an endpoint.

### 5. Bounded, rate-limited, and honest about failure

6 s minimum between fetches; 25 per run by default; the 20 GB store cap and
3-attempt limit shared with `media_preservation`; and a `rate_limited`
response stops the pass rather than pushing through it. Failures are
classified for the dashboard — `private`, `unavailable`, `geo_blocked`,
`rate_limited`, `bad_url`, `tool_missing`, `storage_cap`, `timeout` — because
"failed" alone cannot tell the interface whether retrying is sensible.

### 6. Competitor media remains out of scope

This module will not fetch media for a creator that is not an owned profile.
`media_preservation.own_instagram()` already enforces the same scope for
Instagram, and the standing instruction that public metadata access is not
permission to retain third-party media is unchanged. Nothing here is a step
toward a redistributable creator media database.

## Terms and legal posture

`[PRIMARY]` TikTok's Terms of Service prohibit programmatic collection of
content from the platform. This is a **terms** question, not a settled
copyright question: the material is the operator's own copyrighted work,
published by them, recovered into their own archive, and not redistributed.

That distinction is real but it is not a licence. Recorded explicitly:

- The operator accepted this posture for their own content on 2026-10-26.
- It does not extend to third-party content, and the code cannot be
  configured to extend to it.
- **Before any commercialisation**, this requires legal review alongside the
  §2.3 third-party collection policy. An agency operating owned client
  profiles is a materially different posture from a creator operating their
  own, because "owner-triggered" becomes "whoever holds dashboard access
  triggered", and the cookie in the private path would belong to someone
  else. The OAuth-list gate limits the blast radius but does not settle the
  question of consent between an agency and its client.

## Consequences

**Good.** 234 TikTok publications become analysable. Cross-platform
comparison — the same performance on Instagram and TikTok — becomes possible
for the first time, which Phase 2's `publication_links` was built for and has
never been able to exercise.

**Cost.** A dependency that tracks a hostile target: TikTok changes its
player, yt-dlp follows within days, and a stale pin breaks the archive
silently. Declared as `yt-dlp>=2025.1.1` rather than pinned, and the version
is reported through `coverage()` so a stale install is visible.

**Measured, 2026-10-26.** 7 of 234 archived in testing; 0 failures; 1 080×1 920
HEVC and 576×1 024 H.264 renditions both retrieved and probed playable.
TikTok's default rendition is HEVC, which Chrome will not play, so the format
selector prefers H.264 — the first five downloads predate that fix and are
HEVC, analysable but not previewable, and need a forced re-fetch.

**Open.** `[OPEN]` Whether the published TikTok video and the local master of
the same performance differ enough to matter for hook analysis is now
testable and untested.
