# Research brief: getting TikTok video files at volume, lawfully

> ## WITHDRAWN, 2026-11-03, a few hours after it was written
>
> **The central premise was wrong.** This brief says TikTok is
> refusing our server. It is not, and never was.
>
> The collector's classifier filed `requested format is not
> available` under the same outcome as `your IP address is
> blocked`. Some publications are served with an audio track and
> no video rendition; one of those stopped the whole pass, was
> reported as a platform refusal, and — because the candidate
> query had no ordering and no memory of outcomes — it was the
> *same* publication on every run.
>
> Measured after fixing it: **29 of 30 sampled publications offer
> a video stream, 0 refused**, and a real pass acquired 5 of 10
> with `rate_limited: 0`, the rest skipped as "no video offered"
> with their reason recorded.
>
> So questions 1, 2, 4 and 5 are moot: the free route works and
> there is nothing to route around. **Question 6 is still worth
> asking** — whether the free cover frame carries enough signal
> to make downloading worthwhile at all. Question 3 is still a
> live policy question but no longer an urgent one.
>
> Kept rather than deleted, because an external audit found the
> classifier bug by reading the source, and the record of how a
> wrong premise survived four days of being repeated is worth
> more than a tidy directory.

**Written 2026-11-03.** Everything below the line marked *measured*
was observed directly on the dates given. Everything else is
flagged as unknown. Please keep that distinction in your answer:
guesses presented as facts are the specific failure mode this
brief exists to avoid.

---

## 1. What we are trying to do

A private research project studies classical-piano short-form
video to work out what distinguishes a video that does well from a
similar one that does not. It holds metadata (caption, hashtags,
view count, posting time) for **6,758 TikTok publications by 411
other creators**, collected through a paid provider.

Metadata is not enough. Half the interesting questions are about
what a video *looks* like — how it is framed, how bright and
contrasty it is, how fast it cuts, what text is on screen and
when. Those measurements require the **video file**.

Current state: **1,555 of 6,758** have visual measurements. The
rest need the file fetching, measuring, and deleting.

The file is needed for seconds, not kept. The pipeline downloads
it, derives numeric features, and deletes it within six hours; no
third-party media is retained or served.

## 2. Hard constraints — please do not propose anything that breaks these

These are the project owner's own recorded rules (ADR 0010). They
can be changed, but only by his explicit decision, so an answer
that assumes them away is not useful.

1. **No credentials, ever.** No login, no cookies, no session
   tokens, no fake accounts. Public, logged-out content only.
2. **No circumvention of technical access controls.** A video
   that will not resolve logged-out is skipped, never worked
   around. **Rotating proxies or a VPN specifically to evade an IP
   block fall under this** and are out of scope unless your answer
   makes a well-argued case that a particular approach does not.
3. **Transient.** Media deleted within six hours, never served,
   never redistributed.
4. **Rate limited and polite.** This is research with no deadline.
5. **Internal research use only** while private; legal review
   before any commercial use.

Jurisdiction: **United Kingdom**. GDPR applies.

## 3. What has been tried — *measured*

### Route A: fetch from TikTok directly (`yt-dlp`, logged out)

Our Ubuntu server (datacenter IP) requests the public page.

* Worked until **late October 2026**.
* **2026-11-03:** refused on the *first* request of every pass.
  `attempted: 1, acquired: 0, rate_limited: 1`. Five consecutive
  passes, same result, each ending in about ten seconds.
* Conclusion: TikTok is refusing this IP. Not a rate that recovers
  within a pass.

### Route B: the provider downloads it for us

Apify actor **`clockworks/free-tiktok-scraper`** with the paid
`shouldDownloadVideos` add-on. The provider fetches the file and
stores it in an Apify key-value store; we collect it from there.
**Our server never contacts TikTok.**

Input we send:

```json
{"postURLs": ["https://www.tiktok.com/@handle/video/123..."],
 "resultsPerPage": 40,
 "shouldDownloadVideos": true,
 "shouldDownloadCovers": false,
 "shouldDownloadSubtitles": false,
 "shouldDownloadSlideshowImages": false}
```

**This works, and it is unaffected by the block on our IP.**

* **Price, measured:** $0.003 per file delivered. ($0.003 for one
  file, $0.107 for 34, $0.126 for 41, $0.015 for 5.)
* **But the yield collapses within a run.** Videos returned per
  batch of 40, in order over about ninety minutes:

  | batch | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
  |---|---|---|---|---|---|---|---|---|---|---|
  | delivered | 25 | 23 | 17 | 17 | 17 | 17 | 15 | 11 | 6 | 4 |

* A fresh run **half an hour later** opened at **4 of 40** — it
  does not reset between runs. Whether it resets overnight is
  **unknown**; nobody has measured it.
* Total: **152 videos for $0.895**, i.e. $0.0059 per video
  actually delivered, because a batch is billed whether or not the
  files arrive.

### The diagnostic that matters

When a video is not delivered, the item comes back with
`mediaUrls: []` — **but `videoMeta.downloadAddr` is populated with
a working TikTok CDN URL**, and `error` / `errorDescription` are
both `null`. So:

* The video exists and is public.
* The actor reached it and read its metadata.
* **Only the add-on's own copy is missing**, silently.

That points at the download add-on rather than at TikTok refusing
the provider — but we do not know why. This is the single most
valuable thing to explain.

### Route C: a different actor's direct CDN URL

**`apidojo/tiktok-scraper`** returns `video.url`, a direct TikTok
CDN mp4. Measured 2026-11-03:

* It works, and is **ten times cheaper** for metadata ($0.30 per
  1,000 against $3.00), with exact view counts rather than rounded.
* **Our server can fetch that CDN URL successfully** — HTTP 200,
  `video/mp4`, 22 MB — even though the same server is refused by
  `tiktok.com` itself. The CDN does not appear to apply the same
  block.
* Note: this actor returned `{"noResults": true}` sentinels while
  still billing from 2026-10-26 to 2026-11-03, then started
  working again. Reliability over time is unproven.

**We have not adopted this**, because the project's own position
is that fetching TikTok's CDN ourselves is "this project doing the
access itself", whereas the add-on means "the provider delivers
the file under terms it has already agreed". Whether that
distinction is real is **question 3 below**.

## 4. Questions, in order of usefulness

**Q1. Why does `clockworks/free-tiktok-scraper`'s
`shouldDownloadVideos` add-on return empty `mediaUrls` while
`videoMeta.downloadAddr` is populated and no error is set?**
Is this a known issue? Is it a quota on the add-on, on the
key-value store, on concurrent runs, or something else? Is there
an input parameter — `videoKvStoreIdOrName`, a proxy
configuration, a concurrency or memory setting, smaller
`postURLs` batches, longer gaps — that measurably improves the
delivery rate? Primary sources preferred: the actor's input
schema, its changelog, Apify's docs, the actor's issues page.

**Q2. What else reliably delivers TikTok video files at volume?**
We need roughly 5,000 files over days or weeks, at a sane price.
Other Apify actors, or other commercial providers, that return the
*file* (or a URL they host) rather than a TikTok CDN link. Please
give prices and any evidence of reliability at thousands of items.

**Q3. Is fetching a provider-supplied TikTok CDN URL materially
different from scraping TikTok directly?**
This is the policy question the owner must decide, and he wants it
argued rather than asserted. Relevant angles: TikTok's Terms of
Service as they apply to an unauthenticated GET against
`*.tiktokcdn.com`; whether a CDN serving HTTP 200 to an
unauthenticated request constitutes an access control at all;
whether the earlier block on `tiktok.com` makes a successful CDN
fetch "circumvention" in any meaningful sense; UK copyright and
the text-and-data-mining exception for non-commercial research;
UK GDPR lawful basis for processing video containing identifiable
people. Cite primary sources — the actual ToS text, the actual
statute — not blog summaries.

**Q4. Is there any first-party TikTok route?**
TikTok's Research API is believed restricted to academic and
non-profit applicants and to prohibit commercial use. Is that
still accurate in November 2026, and is there any other official
route — Display API, Content Posting API, a commercial data
licence — that provides video files or visual metadata to a
private, non-academic user?

**Q5. Would a residential connection change anything, and is
using one legitimate here?**
The owner has a Windows machine on UK home broadband that could do
the fetching instead of the datacenter server. Two parts: (a) is a
datacenter IP range the likely reason for the block, such that an
ordinary residential connection would simply not be refused; and
(b) does moving the same activity to a residential connection
*after* a datacenter IP was blocked count as circumventing an
access control under constraint 2? Argue both sides.

**Q6. Do we actually need the video file?**
Every item already carries `videoMeta.coverUrl` — one frame,
free, no add-on. What visual measurements can honestly be derived
from a single cover frame plus duration and dimensions? Which of
these would be lost: shot framing, brightness, contrast, colour,
on-screen text and its timing, cut rate, camera movement? If
covers alone would carry, say, half the signal at zero marginal
cost, that may beat solving Q1 at all.

## 5. What a good answer looks like

* Primary sources cited and linked. Where only a secondary source
  exists, say so and say what would settle it.
* Measurements separated from inference. If something is untested,
  say "untested".
* For Q3 and Q5, a recommendation **with the counter-argument
  stated**, not a verdict.
* Concrete next experiments, cheap ones first, with what each
  would cost and what it would prove.

## 6. Environment, for anything you propose

* Ubuntu server, always on, datacenter IP, Postgres, Python 3.14.
* Windows machine, intermittent, UK home broadband, RTX 2070
  SUPER (8 GB) for local vision work.
* Apify account, $30/month cap, currently about $10 remaining in
  its billing month.
* Three long-running processes only; no new infrastructure
  without a strong reason.
