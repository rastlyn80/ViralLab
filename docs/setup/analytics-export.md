# Exporting analytics by hand — what to get, and why

Two things this system cannot obtain on its own, and both need you for a
few minutes.

1. **TikTok retention.** TikTok's Display API returns views, likes,
   comments and shares and nothing about watch time (ADR 0001), and the
   Research API is permanently out of scope. Average watch time and
   "watched full video" exist only inside TikTok Studio — and, as of
   2026-10-26, only on its **per-video** screen, not in the bulk Content
   download (§1a). Without them, half the project's retention science has
   no TikTok side at all.
2. **Instagram paid delivery.** Readable through the Marketing API, but
   only with an `ads_read` scope the current credential does not have
   (ADR 0008 §4). Granting it is the better fix and is described in §3
   below; an Insights export is the stopgap.

Everything imported this way is tagged `provenance = 'manual_export'` and
stored apart from API readings, so a transcribed number can never be
mistaken for a measured one.

---

## 1. TikTok Studio — the important one

> **Measured 2026-10-26 `[PRIMARY]`.** The owner supplied a real export
> from this path. It contains **no retention metrics at all**. Its full
> header is:
>
> `Time, Video title, Video link, Post time, Total likes, Total comments,
> Total shares, Total views`
>
> That is the same metric set TikTok's Display API already returns, so a
> Content-tab export **does not advance the retention science**. Verified
> further: the exported figures agree with the API to within **0.1%**
> across all 15 rows (largest divergence 37 views in 25,131), which is a
> useful confirmation that the API numbers are trustworthy — and the
> reason this export adds nothing beyond that.
>
> **Average watch time and "watched full video" are on the per-video
> analytics screen**, not in the Content download. See §1a.

**Where:** [tiktok.com/tiktokstudio](https://www.tiktok.com/tiktokstudio) →
**Analytics** → **Content** tab → set the date range → **Download data**.

**Date range:** as far back as it will go. TikTok Studio's analytics window
is limited and rolls forward, so the oldest data is the data that
disappears first. If it will only give you 60 days at a time, do it in
chunks — each file becomes its own import and they do not conflict.

**Format:** if you are offered CSV, take CSV. If you only get `.xlsx`, open
it and *File → Save as → CSV*. No spreadsheet library is installed on the
server for this on purpose; converting takes one click and saves a
dependency.

**Columns that get imported.** You do not need to edit anything — send the
file as it comes. These are the headings the parser recognises:

| what it is | headings recognised |
|---|---|
| **which post** (required) | Video link, Post link, Link, URL, Video ID, Post ID |
| when it was posted | Post time, Publish time, Date |
| title / caption | Video title, Post title, Caption |
| **average watch time** | Average watch time, Avg watch time, Average time watched |
| **watched full video** | Watched full video, Full video watch rate |
| total play time | Total play time, Total time watched |
| views | Total views, Video views, Views |
| likes / comments / shares / saves | Total likes, Total comments, Total shares, Total saves |
| new followers | New followers, Followers gained |
| reach | Reach, Unique viewers |

The two bolded rows are the whole reason for doing this. Everything else
the API already supplies.

**If a heading is not in that list** it is recorded as unimported and named
in the import summary, rather than guessed at. Tell me what it was called
and I will add it — a wrong guess here would attach a watch-time number to
the wrong metric, and nothing downstream could detect that.

**One thing to avoid:** do not rename columns or delete rows to "clean it
up". A row for a post this system has not collected is still useful — it
tells us our coverage is incomplete — and it is kept rather than dropped.

---

## 1a. Where the retention numbers actually are

The Content download does not carry them. On the evidence of the real
export, the only place they appear is the **per-video** analytics screen:
TikTok Studio → **Analytics** → **Content** → click an individual video.
That view shows *Average watch time*, *Watched full video*, *Total play
time* and *New followers* for that one publication.

So there are three options, and the choice is the owner's:

1. **Check for a per-video export button** on that screen. If one exists,
   its CSV is what this importer wants, and the parser already recognises
   all four column names.
2. **Transcribe a sample by hand.** Even 20–30 publications spanning
   strong and weak performers would be enough to test whether the
   Instagram density findings reproduce on TikTok. Tedious, but bounded,
   and it is the only route that exists today. Send them in any format —
   two columns, video link and average watch time, is enough.
3. **Accept that TikTok retention is unavailable** and treat TikTok as an
   engagement-rate-only platform. This is a legitimate choice: TikTok
   still contributes saves/shares/comments per view, and because it is
   unboosted it is the natural control for the Instagram ad confound.

Option 3 costs nothing and still carries real scientific value. Option 2
is the only one that makes TikTok retention possible at all.

**What this means for M1.** Its third acceptance criterion — "at least one
TikTok retention figure exists in the database from an import" — cannot be
met by the Content export, and is not met. The import path itself is
built, tested and proven end to end on real data (15 rows, all matched to
known publications); what is missing is a source that carries the numbers.

---

## 2. Instagram Insights — the stopgap

Lower priority. The API already returns views, reach, likes, comments,
saves, shares, average watch time and skip rate for your reels, so an
export mostly duplicates it. It is worth doing for one reason: the in-app
**"From ads"** breakdown per post, which the API does not expose in any
form.

**Where:** Instagram app → **Professional dashboard** → **Insights** →
**Content you shared** → choose a reel → the per-post breakdown.

If you can export to a file, export it. If the breakdown is only visible
on screen, a screenshot per post is genuinely useful but is **not** an
import — I would have to transcribe it, and transcription is exactly the
error-prone step the `manual_export` tag exists to flag. Prefer §3.

---

## 3. Better than either: grant `ads_read`

This removes the need for the Instagram half of the manual work entirely,
and it is the only route to the *real* paid/organic split rather than the
upper bound this system currently derives.

What is needed, concretely:

- The ViralLab app re-authorised against your Facebook account with the
  **`ads_read`** permission added to the scopes it already has
  (`pages_show_list`, `business_management`, `instagram_basic`,
  `instagram_manage_insights`, `pages_read_engagement`).
- The ad account that runs the boosts has to be visible to that login —
  if the boosts are made in-app under your own account, it already is.

`scripts/authorize_instagram.py` runs the flow. Note that Ads Insights is
read from a **User** token, not the Page token this project currently
stores (ADR 0005), so this adds a credential rather than replacing one.

What it buys, per boosted post per day: spend, impressions, reach, and the
paid watch-time distribution (`video_p25` … `video_p100`). With that, the
ad-exposure covariate stops being "exposure that is neither organic
Instagram nor Facebook" and becomes "paid impressions".

---

## 4. Importing a file once you have it

```
python scripts/provision/import_analytics.py \
    --file  tiktok_content_2026-10-26.csv \
    --source tiktok_studio \
    --platform tiktok \
    --exported-at 2026-10-26
```

`--exported-at` is **the date you downloaded the file**, not today's date
if those differ. It is what the figures are age-anchored against, and a
file exported in March describes March no matter when it is imported
(CLAUDE.md §3.8a).

The importer prints what it mapped, what it ignored, how many rows matched
a known publication and how many did not. Re-running it on the same file
is a no-op — imports are idempotent on the file's own bytes — so there is
no harm in running it twice.
