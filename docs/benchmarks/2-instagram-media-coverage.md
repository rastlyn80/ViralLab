# Instagram media preservation — coverage and automation

2026-10-24. Instagram's `media_url` is a signed CDN link with a short life
and the window does not reopen on demand, so media either gets preserved on
the pass that discovers it or not at all.

## Reconciling 261 / 258 / 28

The corpus previously reported **261 media files**, **258 fingerprints** and
**28 publications with no media URL**. Those three numbers did not add up,
and the discrepancy was a real defect rather than a rounding artefact.

**The 261 vs 258 gap: three image posts were stored as video.** The files
`ig_18197180011344666.mp4`, `ig_18086279405356907.mp4` and
`ig_18097633825803891.mp4` are JPEGs with an `.mp4` extension —
`ffprobe` reports `format_name=jpeg_pipe`. They were marked
`media_status='available'` because the old validation only asked whether
ffprobe *succeeded*, and **ffprobe succeeds on a JPEG, reporting a video
stream**. Only the absence of a duration distinguishes them.

They are now `media_status='unsupported'`, reason
`not_a_video:no_video_stream` — an honest description, not a failure, and not
counted as a coverage gap. Validation now requires a video stream **and** a
positive duration; the regression test
`test_image_saved_as_mp4_is_rejected` pins it.

**The 28: a genuine platform restriction.** Logged directly from the live
endpoint (`[PRIMARY]`, empirical, per CLAUDE.md §2.4). All 28 report:

| field | value |
|---|---|
| `media_type` | `VIDEO` (28/28) |
| `media_product_type` | `REELS` (28/28) |
| `thumbnail_url` | present (28/28) |
| **`media_url`** | **absent from the response entirely** (28/28) |

So these are Reels that Instagram acknowledges and returns metrics for,
while withholding the media URL. Nothing in the pipeline failed; there is
nothing to download. They are `media_status='absent'`, reason
`api_restricted:no_media_url`, and the preserver does not waste attempts on
them.

`[OPEN]` Why these specific 28. Licensed audio is the common explanation for
Instagram withholding `media_url`, but that is `[SECONDARY]` and is not
asserted here. What would settle it: comparing these publications' audio
attribution against the rest of the corpus. It changes nothing operationally
— the media is unavailable either way — so it is recorded rather than chased.

### Current coverage

| | count |
|---|---|
| Instagram publications | 289 |
| `available` (genuine video, fingerprinted) | **258** |
| `unsupported` (not a video — nothing to preserve) | 3 |
| `absent` (API withheld the media URL) | 28 |
| **preservable** (excludes non-video types) | **286** |
| **coverage of preservable** | **90.2%** |
| exhausted all retry attempts | 0 |
| on disk | 736 MB, 542 GB free |

The denominator deliberately excludes `unsupported`: counting an image post
as a missing video overstates the gap and makes the number impossible to act
on.

## Was the downloader triggering for new Reels? No.

It was a hand-run provision script. Nothing invoked it, so a Reel collected
on a day nobody ran it by hand lost its media permanently once the URL
expired. That is now fixed:

`server/collectors/media_preservation.py` is a deployed module, and
`collect_instagram_once` calls it at the **end** of every Instagram
collection pass — on the same schedule as collection, because that is the
only schedule that works.

### A media failure can never disturb metric collection

Metrics are the irreplaceable record: a snapshot missed today is gone
forever, while media is best-effort and retried tomorrow. So:

- the metrics run is **committed before** preservation starts, so a media
  problem cannot roll back snapshots that were already collected
- preservation runs inside `_preserve_media_quietly`, which catches
  everything, logs it, rolls back its own session and returns normally
- the daily loop therefore records the pass as `ok` on the strength of the
  metrics, with media problems appearing as a logged warning

Preservation can also be switched off entirely with
`VIRALLAB_PRESERVE_MEDIA=0` without touching collection.

## Safeguards

| safeguard | behaviour |
|---|---|
| **fresh URLs only** | a stored URL is assumed dead and never retried |
| **validation** | video stream **and** duration > 0.2 s required; bad files are deleted, not recorded |
| **deduplication** | by SHA-256; identical media under two ids is stored once and the second row points at the first |
| **bounded attempts** | `MAX_ATTEMPTS = 3`, tracked in the database, so a broken item cannot be retried forever |
| **retry spacing** | a failure is not re-attempted for 20 h, i.e. the next daily pass |
| **storage cap** | 20 GB; hitting it stops the run and does **not** consume the item's attempts, since that is our limit and not the item's fault |
| **per-run ceiling** | 40 saves, so one pass cannot monopolise the night |
| **resumable** | anything already `available`, `unsupported` or `purged` is skipped; the existing 258-file archive is never re-downloaded |
| **quota respect** | runs inside the existing Instagram BUC accounting (`snapshot_policy.py`); preservation adds no insight calls |

## Reason taxonomy

The point of classifying rather than lumping together: only one of these is
worth retrying.

| class | meaning | retried? |
|---|---|---|
| `api_restricted` | the API returned the post but withheld its media | no |
| `not_a_video` | an image or carousel — nothing to preserve | no |
| `download_failed:url_expired` | signed link died (403/410); a fresh URL is fetched next pass | **yes** |
| `download_failed:http_*` / `*:empty` | transient | **yes** |
| `not_a_video:no_video_stream` | downloaded, but not video | no |
| `deduplicated:<id>` | identical media already stored | n/a |

## Tests

`tests/server/test_media_preservation.py`, 15 tests, covering: the
JPEG-as-mp4 rejection, HTML-error-page rejection, each reason class, bounded
attempts, retry spacing, resumability, the storage cap not consuming
attempts, deduplication, duration backfill, and that `coverage()` excludes
unsupported types from its denominator.
