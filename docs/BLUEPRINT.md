# ViralLab Blueprint — from here to the finished product

**Written 2026-10-26 at commit `e4782a8`.** Supersedes the Phase 7–12
outline in `DEVELOPMENT_PLAN.md` §7 once adopted; the next development
session should reconcile the two rather than keep both.

This document does three things. It states where the project actually is,
with numbers. It describes a measurement problem the owner raised that
reshapes the science, with the first test of it already run. And it lays
out every stage to a finished creative suite, with each capability graded
by the evidence it needs — because the one rule that does not bend is that
nothing ViralLab recommends may rest on anything other than evidence
gathered from the videos, on both TikTok and Instagram.

Every factual claim below is tagged. `[PRIMARY]` was measured on this
hardware and corpus or verified against platform-official documentation.
`[SECONDARY]` is from memory or a non-official source and **must be
verified before code depends on it**. `[OPEN]` is a question nobody has
answered yet.

---

## 0. Where we are

| Capability | State | Evidence |
|---|---|---|
| Own Instagram corpus | 385 publications, **258 with media**, **286 with retention metrics** (49 before M1; the rest had simply never been requested) | `[PRIMARY, 2026-10-26]` |
| Own TikTok corpus | 234 publications, **225 with media** (5 geo-blocked, 1 deleted, 3 transient) | `[PRIMARY]` |
| Competitor corpus | **53 creators, 60 publications**, TikTok + Instagram, $0.09 spent | `[PRIMARY]` |
| Deterministic hook features | 21 visual/audio measures on **258** videos and background structure on 258, after the 2026-10-26 backfill. Was 48 — the previous claim of 258 was wrong, and closing the gap took 385 s of CPU | `[PRIMARY, 2026-10-26]` |
| OCR | RapidOCR on **258** after the 2026-10-26 backfill (was 48; it runs on CPU and never needed the GPU box); **no OCR measure relates to retention at n=258 either**, and the proxy is confirmed not to be reading text | `[PRIMARY, 2026-10-26]` |
| Speech | Whisper small, gated; speech present in 4 of 48 | `[PRIMARY]` |
| Local vision | Gemma 3 4B on the RTX 2070, 7.9 s/video, $0; **one reliable field of eight** (`text_overlay_present`) | `[PRIMARY]` |
| Retention science | At n=258: **`background_edge_density` −0.401** and `edge_density` −0.389 vs skip rate (q<0.001); `text_region_score` is a weaker reading of the same thing (−0.292, and −0.096 once density is controlled). The n=48 figure of −0.563 was inflated by the small sample. | `[PRIMARY, 2026-10-26]` |
| Spending control | Per-category caps enforced, runtime authorisation separated from code, real costs captured | `[PRIMARY]` |
| Credentials dashboard | Write-only, whitelisted, live | `[PRIMARY]` |
| Competitor media | **None downloaded, none analysed** | — |
| Creator report, comparisons, opportunities, strategist | **Not started** | — |

**Corrected and then closed, 2026-10-26.** The feature-coverage rows
above said 258 and were wrong — `video_features` held the deterministic,
OCR, background and speech sets for **48 videos each**. Feature
extraction, not data access, was the binding constraint on every retention
finding in the project.

The two CPU-only extractors have since been backfilled across all 258
preserved publications (290 s deterministic, 95 s background, 0 failures),
and **Part 5 was re-run at n = 258**. The larger sample overturned part of
its conclusion: `text_region_score` fell from −0.564 to −0.292 and lost
the head-to-head against generic density, which it had appeared to win at
n = 46. See `docs/benchmarks/6c-visual-density.md` §A.4–A.5.

OCR was backfilled too, once it became clear RapidOCR runs on CPU where
the media lives and never needed the GPU box: 207 publications, 0
failures, 856 s. **Speech (48) and vision (54) are now the only coverage
gap**, and they do need the Windows worker.

Two platform limits bound everything that follows and must be designed
around rather than wished away:

- `[PRIMARY]` TikTok's Display API returns **no retention metrics** — views,
  likes, comments, shares only. Retention-based science is Instagram-only
  unless the owner supplies TikTok Studio exports (see M1).
- `[PRIMARY]` Competitor data carries **one observation per publication at
  an unknown age**. No competitor retention metric exists anywhere, and
  none will be manufactured.

---

## 1. The ad problem, and what it does to the science

> **Corrected 2026-10-26 by M1.** Three claims in this section were wrong
> and are struck through below rather than deleted, because the reasoning
> that followed from them is still worth seeing. In short: Instagram's
> organic metrics were **already organic**, so there was nothing to
> subtract; the campaign is **three and a half months old and boosts a
> minority of posts**, not several months and every post; and the
> contamination of the retention corpus is correspondingly smaller than
> feared. See `docs/adr/0008-paid-organic-separation.md` and
> `docs/benchmarks/6c-visual-density.md`.

~~The owner has run a **$1/day Instagram ad that rotates to each new
post** for several months.~~ **Measured `[PRIMARY, 2026-10-26]`:** of 385
own-account publications, 373 resolve and **29 carry a paid residual share
≥ 0.25**. Three are isolated events (2025-06-17, 2026-03-07, 2026-06-30);
the rest form a continuous run from **2026-07-13** to the present. August
boosted 7 of 40 publications, so the ad does not rotate to every new post.
Every recent Instagram metric is therefore a blend of paid and organic
exposure *for the minority of posts that were boosted*, and the blend
differs per video.

### 1.1 Why this matters more than it looks

Views are the obvious casualty, and the least important one — ViralLab
already refuses to rank on raw views. The real damage is to **per-viewer
behaviour metrics**, which the retention science depends on:

- `reels_skip_rate` and `ig_reels_avg_watch_time` are computed over
  everyone who saw the video, and an ad-delivered cold viewer behaves
  differently from a follower.
- ~~The ad rotates on each new post, so a video's paid exposure is roughly
  proportional to the time until the next post.~~ **Disproved
  `[PRIMARY, 2026-10-26]`:** boosting is selective — 7 of 40 publications
  in August — so paid exposure is not a function of posting rhythm, and
  the reasoning that followed from it does not hold.
- The ad also recruited followers over months, so the organic audience
  itself drifts in composition. A slow confound, handled by time controls.

### 1.2 First test — superseded by the measured covariate

> **Superseded 2026-10-26.** The gap proxy below was the best available
> before the surface decomposition existed. It pointed the right way on
> both findings but understated the `edge_density` correction, and it was
> computed over 47 publications almost none of which was actually boosted
> — so what it mostly measured was organic accumulation, exactly as its
> own caveat warned. The measured results are in
> `docs/benchmarks/6c-visual-density.md` §A.4; the headline holds at
> **−0.528** and `edge_density` falls to **−0.279**.

Using *gap to next post* as a proxy for paid exposure, derivable from
`posted_at` alone, on the 47 retention videos that have one:

| association | rho | p |
|---|---|---|
| gap-to-next-post vs `reels_skip_rate` | **−0.378** | **0.010** |
| gap-to-next-post vs `text_region_score` | +0.157 | 0.306 |
| gap-to-next-post vs `edge_density` | +0.341 | 0.022 |

| finding | raw | controlling for the gap |
|---|---|---|
| `text_region_score` vs skip rate | −0.558 | **−0.545** |
| `edge_density` vs skip rate | −0.435 | −0.352 |

**The outcome metric is contaminated** — the ad proxy relates to skip rate
at p = 0.01. **The headline finding survives** — `text_region_score` is
unrelated to the proxy and keeps its association. **The secondary finding
weakens** — part of what `edge_density` was measuring was which videos sat
under the ad longest. The docs for Part 5 must be amended to say so.

This proxy is crude. Videos with a long gap also accumulate more *organic*
exposure, and the ad's own delivery optimisation may select viewers who
watch. The proxy establishes that the confound is real; it cannot yet
separate the mechanisms.

### 1.3 What can actually be separated

> **Withdrawn 2026-10-26.** The subtraction below assumed the organic
> media-insights endpoint reports a paid-inclusive total. It does not.
> Meta documents the opposite: *"Metrics such as `comments`, `likes`,
> `views`, and `total_interactions` report organic interaction metrics
> only; interactions on ads containing a media object are not counted."*
> Building on this formula would have subtracted paid exposure from a
> figure that never contained it. `[PRIMARY]`, ADR 0008 §2.

~~A boosted Instagram post becomes an ad with its own `ad_id` in the
Marketing API […] then for every boosted post:
`organic ≈ media_insights(total) − ads_insights(paid)`~~

**What is actually true `[PRIMARY, 2026-10-26]`.** Organic needs no
derivation — `views`, `likes`, `comments`, `saved`, `shares` and
`total_interactions` are already organic-only. What has to be *derived* is
the paid side, and it comes from the surfaces that sit outside the organic
count:

    crossposted_views      == views + facebook_views   (exact, measured)
    residual_surface_views == total_views − crossposted_views

`total_views` is documented as spanning "all surfaces, including views
from promoted/boosted/ad media and Facebook". The residual was exactly **0**
on every unboosted publication measured. It is therefore an **upper bound**
on paid exposure rather than the paid figure — "all surfaces" names
promoted media as one member, not the only one — and the field is called
`residual_surface_views`, never `paid_views`.

The important practical consequence: this needs **no new permission**. It
comes from the Page token the project already holds.

**Ads Insights is unreachable today `[PRIMARY, 2026-10-26]`.** The stored
credential is a Page token whose scopes are `pages_show_list`,
`business_management`, `instagram_basic`, `instagram_manage_insights`,
`pages_read_engagement`, `public_profile` — no `ads_read` — and
`/me/adaccounts` is not an edge of a Page token at all. The collector for
it is written and inert (`server/collectors/meta_ads.py`); granting the
scope is an owner action, described in `docs/setup/analytics-export.md` §3.

Where the API cannot give it, the in-app Instagram Insights screen shows a
"From ads" breakdown per post. A **manual analytics import** — the owner
exports or screenshots, ViralLab ingests and versions it — is a legitimate,
if laborious, fallback, and the same mechanism would bring TikTok Studio's
per-video *average watch time* and *watched full video %* into the system,
which the TikTok API never will.

### 1.4 Metrics that are intrinsic to the video, boosted or not

None are perfectly immune, but these are robust in this order:

1. **Organic-only rates**, once the paid share is subtracted (M1).
2. **Within-platform, within-period relative performance** — a video
   against its own creator's trailing baseline over the same boosted
   months. The ad's effect is roughly constant across neighbours, so it
   cancels in the ratio; the gap covariate absorbs what does not.
3. **Engagement rates per view** (saves/view, shares/view, comments/view),
   which cold viewers also produce but at lower rates — contaminated in
   level, more stable in rank.
4. **TikTok as the unboosted control.** `[OPEN — confirm]` the owner has
   not boosted TikTok. If so, the 156 cross-platform pairs already matched
   in `publication_links` are a natural experiment: the same performance,
   boosted on one platform and not the other. Metric definitions differ
   across platforms, so the comparison is of *ranks within each platform*,
   never of absolute figures.

### 1.5 The rule going forward

Every retention or engagement analysis on the owner's Instagram account
carries an **ad-exposure covariate** — the proxy until M1 delivers the real
split, the real split afterwards — and reports results with and without it.
A finding that disappears under the control is reported as having
disappeared. The Phase 8/9 experiment design must hold the ad constant
across arms or randomise it, or it measures the ad.

---

## 2. The evidence grading that governs every feature

Every statement ViralLab shows a user carries one of five grades, stored
with the statement, never inferred at display time:

| grade | meaning | may drive a recommendation? |
|---|---|---|
| **Observed** | A direct measurement on specific publications | as a description, yes |
| **Associated** | A correlation that survived correction and the ad covariate, with n, rho, CI | as a hypothesis to test, yes |
| **Hypothesised** | Suggested by the data but not tested, or n too small | only as an experiment proposal |
| **Validated** | Survived a pre-registered own-account experiment | yes |
| **Unavailable** | The data or a reliable measure does not exist | never — and the gap is shown |

Withheld measurements (`vision_piano_or_keyboard_visible` and friends) are
**Unavailable** until a held-out benchmark promotes them. A feature's grade
is the thing the strategist reads before it is allowed to say anything.

---

## 3. Milestones

Sequential. Each has acceptance criteria that are demonstrated, not
asserted. Cost figures are measured where tagged and projections otherwise.

### M1 — Measurement integrity *(before any new intelligence feature)*

The science is only as good as the outcome variable, and §1 shows the
outcome variable is contaminated.

- Verify the Marketing API path for paid/organic separation against the
  official docs; record the verification as an ADR with the doc URL and
  date (CLAUDE.md §2). Check whether the existing token can reach Ads
  Insights. If it can, ingest per-ad metrics and link them to media ids.
- Store **organic-derived metrics as separate snapshot fields**, never
  overwriting the raw totals. Age-anchor them (`views@7d` etc.) per
  CLAUDE.md §3.8a — the anchors have been designed since Phase 1 and are
  still not populated for most videos.
- Add the ad-exposure covariate (gap proxy now, paid share later) to
  `retention.py` and `visual_structure.py`, re-run Part 5, amend
  `docs/benchmarks/6c-visual-density.md` with the §1.2 result.
- Build the **analytics import** path: owner-supplied TikTok Studio and
  Instagram Insights exports, versioned, with provenance `manual_export`
  and the export date. This is the only route to TikTok retention.
- Confirm with the owner: ad start date, per-post spend, whether TikTok was
  ever boosted, whether Ads Manager access can be granted.

*Acceptance:* every own-account publication has an ad-exposure value or an
explicit `unknown`; Part 5 is re-reported with the covariate; at least one
TikTok retention figure exists in the database from an import.

#### Status, 2026-10-26 — two of three acceptance criteria met

> Followed immediately by the hook-feature backfill the owner prioritised
> over M2: the CPU-only extractors now cover all 258 preserved
> publications, Part 5 was re-run at n = 258, and the result overturned
> §6 of `docs/benchmarks/6c-visual-density.md`. OCR, speech and vision
> remain at 48/54 and are the next coverage gap.

| criterion | state |
|---|---|
| every publication has a value or an explicit `unknown` | **met** — 385 of 385; 319 resolved, 66 refused with a stated reason (54 have zero total views, 12 have an unattributable gap) |
| Part 5 re-reported with the covariate | **met** — `docs/benchmarks/6c-visual-density.md` §A.1–A.8 |
| ≥ 1 TikTok retention figure from an import | **NOT met** — and now known to be blocked by TikTok, not by the owner |

What was built: ADR 0008 (verification), migration `c81f3a7e04d2`
(`metrics_snapshots.total_views/facebook_views/crossposted_views`,
`ad_exposures`, `analytics_imports`, `imported_metrics`),
`server/analytics/ad_exposure.py` (the covariate, with source-mixing
refused), `server/analytics/imports.py` + `scripts/provision/
import_analytics.py` (the manual import), `server/collectors/meta_ads.py`
(Track B, inert until `ads_read`), and the covariate wired into
`retention.py` and `visual_structure.py`.

What the owner has to do, and nothing else will substitute:

1. ~~Export TikTok Studio content analytics.~~ **Done 2026-10-26, and it
   does not contain retention.** `[PRIMARY]` The Content-tab download
   carries only `Time, Video title, Video link, Post time, Total likes,
   Total comments, Total shares, Total views` — the same set the Display
   API already returns. 15 rows imported, all matched to known
   publications, and they agree with the API to within 0.1%, which at
   least confirms the API figures are sound. Average watch time and
   "watched full video" live on TikTok Studio's **per-video** screen.
   Options are in `docs/setup/analytics-export.md` §1a; the only one that
   yields TikTok retention today is transcribing a sample by hand.
2. **Grant `ads_read`** if the real paid split is wanted rather than the
   upper bound. §3 of the same document.

Answers to the questions M1 was supposed to ask, where measurement could
answer them instead: the campaign began **2026-07-13** and is selective
(§1); per-post spend is **unknown** and needs `ads_read`; TikTok carries
**no equivalent surface metric**, so the owner's statement that it has
never been boosted cannot be checked against the API and is recorded as
`[SECONDARY]`.

### M2 — Competitor media, transient and analysed *(old brief Stage 3)*

Implements `docs/MULTIMODAL_PIPELINE.md`'s transient design, which has
existed on paper since Phase 4.

- Acquisition is a job with a lease; media lands in a quarantined directory
  outside `MEDIA_ROOT`, outside backups, unreachable by the media-serving
  endpoint; the six-hour retention cap is enforced by a reaper that runs on
  crash, cancellation and lease expiry, with an audit row per deletion.
- Source and permission are recorded per file. `[PRIMARY, 2026-10-26]` both
  validated scrapers advertise a paid `video-download` add-on event; that
  route is the provider delivering media under its own terms and is the
  default. Direct platform fetching of competitor media is **not** done.
- The Windows worker receives frames and audio windows, never whole files,
  through the existing authenticated `/api/frames` path extended with an
  audio equivalent. Batched by modality (CLAUDE.md §5.8). Retries bounded;
  a retry can never re-download or re-bill.
- Derived features persist with the same `video_features` versioning as
  own content. The file is gone within the hour.

*Acceptance:* ≥ 20 competitor publications across ≥ 10 creators analysed
end to end; zero competitor files older than six hours on disk at any
audited moment; the frames endpoint refuses a competitor media id.

#### Status, 2026-10-27 — COMPLETE as specified

| criterion | state |
|---|---|
| ≥ 20 publications across ≥ 10 creators analysed end to end | **met** — **23 publications across 20 creators**, **1,127 feature rows** stored, 0 extraction failures |
| zero competitor files older than six hours | **met, demonstrated** — one file's deadline was forced into the past and the running service deleted it unaided within 15 s; every audit since reports `guarantee_holds: true` with zero files remaining |
| frames endpoint refuses a competitor media id | **met** — it already did; `is_own_account` returns 403 |

The full audit ledger, which is the real evidence: **31** files released
`features_persisted` (112.8 MB), **17** `crash_recovery` (75.0 MB — the
honest record of the batch bug below), **1** `expired` (5.8 MB). Zero
files remain on disk and every byte is accounted for by a row.

**Cost: $0.1795** of the $3.00 `competitor_media` ceiling, at roughly
$0.003–0.004 a file. Discovery has spent $0.15 of its $2.00.

Built: `transient_media` (migration `e2b57c9a013f`),
`server/collectors/transient.py` (quarantine, TTL, four-way release,
two-directional sweep, `audit()`), `server/collectors/competitor_media.py`
(provider acquisition under the `competitor_media` budget category), and
the sweep wired into the existing reaper loop — it runs every 60 s.

**Costs, measured `[PRIMARY, 2026-10-27]`.** One file: **$0.003**, the
same as a metadata run, so the download add-on adds no separate per-file
charge at this volume. Twenty publications projects to **$0.06** against
the $3.00 `competitor_media` ceiling. Money is not the constraint here.

**Provider facts verified against the actor's own input schema** (`GET
/v2/actor-builds/{id}`, `clockworks/free-tiktok-scraper`): `postURLs`
fetches named publications; `shouldDownloadVideos` is marked `($)` by the
provider, confirming the blueprint's `[SECONDARY]` add-on claim; the
file arrives in Apify's key-value store and needs the API token to
retrieve, which an unauthenticated fetch reports as a bare 403.

**A limit on what "transient" can honestly mean.** The same schema says
unnamed key-value stores are **deleted after 7 days**. Our six-hour
guarantee covers our disk; the provider keeps its own copy for up to a
week and nothing we do shortens that. The claim is therefore "ViralLab
does not retain competitor media", not "no copy exists" — recorded in
`PERMISSION_BASIS` on every row, because the second sentence is what a
legal review under §2.3 actually needs.

**Three bugs that only appeared at realistic scale**, all now under
regression test:

1. **The sweep trusted deleted rows.** Quarantine paths derive from the
   video id, so a re-acquired file lands where a *deleted* row recorded
   it; the "known files" set included deleted rows, so the bytes were
   skipped and sat on disk under an audit record saying they had been
   removed. A false audit is worse than none.
2. **`path` was globally unique**, so a publication could never be
   re-acquired while its audit history existed. Now a partial unique
   index over live rows, which is the invariant that actually matters.
3. **A 25-file batch lost 17 paid downloads to its own reaper.**
   Registrations committed only after the loop, so for the batch's
   duration every file was invisible to the reaper's session and was
   deleted as an orphan. Fixed with per-file commit *and* a ten-minute
   `ORPHAN_GRACE`: the bytes necessarily reach disk before the row is
   durable, so narrowing that window is not the same as closing it.

**What is deliberately not done.** No vision — M3 has not established
that any model can be trusted, and competitor frames reaching a cloud
provider needs both a `cloud_vision` cap (currently $0.00) and a terms
check that is recorded rather than assumed. No speech — Phase 0B
measured Whisper hallucinating on music-only audio. Competitor
publications therefore carry exactly what own publications get from the
CPU path, under the same method versions, so the two are directly
comparable.

**And the criterion itself is too weak.** 23 publications across 20
creators satisfies it while `creators_with_20_or_more` is **0** — the
corpus is still one publication per creator, so no creator baseline can
be computed and no relative performance derived. `competitor.coverage()`
reports both numbers for that reason. M5 and M6 need depth, not breadth,
and acquiring it costs about $0.003 a file.

### M3 — Vision that can be trusted *(old brief Stage 4.2)*

The Gemma 3 4B benchmark returned a lift of **−0.020** over a constant
answerer. Five of six priority fields failed. This milestone exists to fix
that honestly, and the owner has now authorised external models in
principle, which widens the options.

- A **fresh held-out label set**: 40 own + 40 competitor openings, labelled
  from full frame strips before any inference, stratified by platform and
  setting. The existing 24 become the *tuning* set and are never scored
  again.
- Candidates, each measured for precision, recall, specificity, accuracy,
  baseline lift, abstention, latency, VRAM and cost: Gemma 3 4B with a
  negative-example prompt; Qwen2.5-VL 7B Q4 (fits at ~7.6 GB, measured in
  Phase 0B, co-residency impossible); one cloud vision provider on **own
  media only** once the owner sets a `cloud_vision` cap. Competitor frames
  go to a cloud provider only if the provider's terms and the source's
  permit it — recorded, not assumed.
- Deterministic detectors where the signal is structural: a piano keyboard
  is a periodic black-and-white pattern and sheet music is five parallel
  lines; both are likely detectable from pixels with far higher specificity
  than a 4B language model. Measured, not assumed.
- Promotion rule, written down before scoring: a field enters analytical
  use only with specificity ≥ 0.80 and lift ≥ +0.10 on the held-out set.

*Acceptance:* a per-field table on the held-out set for every candidate;
promoted fields listed; everything else still withheld.

#### Status, 2026-10-27 — COMPLETE as specified

| criterion | state |
|---|---|
| fresh held-out set, 40 own + 40 competitor, labelled from strips before inference | **met** (the competitor 40 span 33 creators, not 40 - the corpus holds 48 publications across 38) — `holdout_labels_own.json`, `holdout_labels_competitor.json`; 80 publications, none among the 24, two halves disjoint, all checked by test |
| per-field table for every candidate | **met** — `docs/benchmarks/6c-vision-benchmark.md`, M3 addendum. Two candidates measured, one measured and failed, two blocked with the reason measured |
| promoted fields listed, everything else withheld | **met** — `visual_structure.VISION_FEATURES` gained `vision_face_visible`; six fields withheld, each with its figure |
| promotion rule applied as pre-registered | **met** — specificity ≥ 0.80 and lift ≥ +0.10 on the pooled 80 |

**What was promoted.** Measured under the prompt the pipeline actually
runs, pooled over all 80:

| field | specificity | lift | status |
|---|---|---|---|
| `vision_text_overlay_present` | 0.917 | +0.138 | confirmed on fresh data; was already promoted |
| `vision_face_visible` | 0.808 | +0.175 | **newly promoted**, with a caveat |
| `vision_sheet_music_visible` | 0.193 | −0.288 | withheld; see the prompt result below |
| `vision_piano_or_keyboard_visible` | 0.100 | +0.012 | withheld |
| `vision_hands_visible` | 0.100 | +0.012 | withheld |
| `vision_performer_visible` | 1.000 | −0.012 | withheld — only 5 negatives in 80, none on the own half |
| `vision_shot_distance` | — | −0.101 | withheld |
| `vision_setting` | — | −0.319 | withheld |

`vision_face_visible` is the weakest thing now in use and the caveat
travels with it in the registry: pooled it clears, but per half it is
0.857 on competitor publications and **0.750 on the owner's own**,
below the bar. The own half is the population recommendations are about.

**The finding worth carrying forward.** A negative-example prompt —
naming the base-rate shortcut, giving a visibility criterion per field,
supplying the measured false positives — takes `sheet_music_visible`
from specificity **0.193 to 0.807** (18 fixed against 2 broken on the
own half, McNemar p = 0.0004; 17 against 0 on the competitor half). It
is still not adopted, because it does not sharpen perception, it moves
the model's whole operating point towards "no": `text_overlay_present`
recall falls to 0.912, `face_visible` specificity to 0.750,
`performer_visible` specificity to 0.400. A prompt is chosen per run,
not per field, so adopting it would gain one field and lose two —
and choosing the better arm per field after seeing the table would be
selection on the test set. Both prompts stay in `vision_task` behind
`VIRALLAB_VISION_PROMPT`; the default remains `original`.

**The brief's central assumption was wrong.** M3 proposed that a piano
keyboard and sheet music were the *easy* fields — "both likely
detectable from pixels with far higher specificity than a 4B language
model. Measured, not assumed." Measured: sheet music is fixable by
prompt alone, and the keyboard is the hardest field in the task. It has
now been attacked three ways — the VLM, the negative-example prompt,
and row-wise autocorrelation — and sits at specificity 0.200 at best.
The model answers from "this is a piano video", not from the pixels,
and the one negative the prompt fixed was the one example the prompt
itself contained.

**Candidates not run, with the reason measured rather than assumed.**
Qwen2.5-VL 7B Q4 needs 7634 MiB (Phase 0B, this card); the desktop
floor measured with `llama-server` stopped is 1126 MiB, leaving
**6864 MiB free** — 770 MiB short, so it cannot load. A reduced-context
or Q3 build is untested. Cloud vision is capped at $0.00 and the owner
has authorised Apify only; competitor frames could not go to a cloud
provider regardless without first checking that provider's terms and
the source's.

**Cost: $0.1616** for the competitor half (40 files at $0.00404,
measured), bringing `competitor_media` to $0.3411 of its $3.00 ceiling
and total spend to **$0.4911 of $5.00**. All 40 files were deleted
after labelling; `guarantee_holds: true`, zero files on disk.

**Two defects found and fixed along the way.** `start_server()` treated
an open socket as a loaded model, so a batch sent to a still-loading
`llama-server` lost its **first 17 of 40** publications to 503s that
were recorded as ordinary per-video failures — indistinguishable, in
the results file, from the model genuinely failing. It now waits on
`/health`, in both the launch branch and the reuse branch, with
regression tests. Separately, the scorer's first draft required a field
to clear the bar on *both halves*; that is a better rule, but inventing
it after seeing which fields it excluded would be post-hoc tightening,
so the pre-registered pooled rule decides and **the both-halves rule
should be written into M4 before its first measurement.**

**Open, and it is the owner's call.** The 80 labels were written by
Claude. Each fixture flags its genuinely ambiguous cases — 10 own, 11
competitor — and those are where a second opinion would move a number.
`vision_face_visible` is in analytical use on that basis.

### M4 — The feature catalogue

One registry of every feature, per modality, with grade, method version,
reliability evidence and platform availability. The strategist, the report
and the comparisons all read this registry rather than hard-coding lists.

Visual (deterministic): density, background structure, contrast, colour,
cuts, motion, camera motion — all `[PRIMARY]` today. Visual (model):
whatever M3 promotes. Audio: onset, RMS envelope, spectral stats, dynamics,
novelty — present; **tempo and climax timing** — to add, deterministic.
Text: OCR words, timing, changes; speech when present; caption length,
question/statement/call-to-action classification from the caption text
(deterministic rules, then measured). Repertoire: **own** repertoire is
known from `performances` (146 rows) — a real asset competitors lack;
competitor repertoire only from captions and TikTok `music` metadata,
graded Observed when stated, never inferred from frames.

*Acceptance:* `GET /api/features/catalogue` returns every feature with its
grade; the UI renders Unavailable as Unavailable.

#### Status, 2026-10-27 — COMPLETE as specified

| criterion | state |
|---|---|
| `GET /api/features/catalogue` returns every feature with its grade | **met** — 79 features, every one carrying a grade and a stated reason for it |
| the UI renders Unavailable as Unavailable | **met** — `#/features`; Unavailable rows are marked, never blank, and each shows why |
| one registry, read instead of hard-coded lists | **met** — `server/features/catalogue.py` |
| grade, method version, reliability evidence, platform availability | **met** — all four per entry |

**The grade is derived, never written down.** No entry in the registry
carries a grade; `grade_for()` computes one from three things a query
can check — whether an extractor exists and has produced rows, whether
`visual_structure.WITHHELD_VISION` withholds the field, and what the
new `feature_evidence` table holds. A hand-written grade would make
the registry a claim. This makes it a measurement, and a stale grade
becomes a query rather than a reading.

Measured on the live corpus:

| grade | count | meaning |
|---|---|---|
| Unavailable | 31 | may never be used, and the gap is shown |
| Observed | 39 | measured; may describe |
| Hypothesised | 0 | — |
| **Associated** | **9** | survived correction *and* the ad covariate |
| Validated | 0 | needs the Phase 8 experiment, which has not run |

The nine: `edge_density`, `contrast_rms`, `brightness_mean`,
`saturation_mean`, `text_region_score`, `background_edge_density`,
`background_grey_entropy`, `subject_edge_density`,
`background_subject_edge_ratio` — all against `reels_skip_rate` at
n=229–247 with duration, age and ad exposure held constant. These are
the published Part 5 §A.4 findings, now stored rather than pasted: the
persisted rho and partial_rho match that table figure for figure.

**`feature_evidence` is new, and it is the point.** Associations were
computed live and printed; the numbers reached
`docs/benchmarks/6c-visual-density.md` by being copied there, so any
grade derived from them was derived from prose. Migration
`a2f49c81b73e` adds the table, `visual_structure.persist_evidence()`
writes to it, and the identity of a finding includes its controls —
the same correlation with and without the ad covariate is two results,
which is the whole of M1.

**Two defects the work surfaced.** The first version of
`persist_evidence` filed Arm A and Arm B under one outcome name, so
both arms collapsed onto the same uniqueness key; the database refused
the write, which is a better outcome than keeping whichever landed
last. The second wrote Arm A's missing ad covariate as "did not cover
enough of this sample" when the truth is that both sides of Arm A are
extracted features and paid exposure cannot contaminate it — the
covariate does not *apply*, which is a different statement and would
have sent a reader to fix something that was not broken.

**31 Unavailable is the honest number, not a shortfall.** 18 are
vision fields the M3 benchmark withheld, 7 are deterministic
placeholders `hookfeat` stores with their own reason so a consumer
asking "is a piano visible" gets a recorded "cannot say" rather than a
missing key it might read as false, and 6 have no extractor at all:
`audio_tempo_bpm`, `audio_climax_at_s`, `caption_length_chars`,
`caption_intent_class`, `repertoire_work`, `repertoire_composer`. Those
six are named in the registry precisely so the gap is visible — a
catalogue built by scanning `video_features` would not mention them,
which is the opposite of what §2 asks for.

**Still to build, and now listed as Unavailable rather than forgotten:**
tempo and climax timing (deterministic, named in this brief), caption
length and intent classification, and reading the 146 rows of own
repertoire out of `performances` into the feature store. Each is a
feature the catalogue already describes and grades Unavailable with the
reason; building one flips its grade without touching the registry.

### M5 — Creator Intelligence Report *(old brief Stage 5)*

For any creator including the owner. Overview, publication intelligence,
content intelligence, evidence and uncertainty — the four sections the
brief specified, with these rules built in:

- Performance is always relative to the creator's own trailing baseline at
  a fixed age where ages are known, and labelled *lifetime approximation*
  where they are not (every competitor).
- Each conclusion links to the publications and measurements behind it and
  carries its §2 grade; contradictory examples are listed, not hidden.
- For the owner, the report shows paid and organic separately once M1
  lands, and the gap covariate until then.
- The owner's report additionally includes repertoire performance, because
  only the owner's repertoire is known.

*Acceptance:* a report renders for `@gabriele_baldocci` on both platforms
and for three competitors of different sizes, with no fabricated metric and
every number traceable by click.

**Addendum, 2026-10-27 — evidence-to-language and an AI-native surface.**
The owner added a mandatory requirement during implementation: ViralLab is
not a technical analytics dashboard, it is an AI creative research
assistant. The science stays rigorous; the surface must be readable by
someone who has never heard of a correlation. The addendum's own acceptance
criteria are folded into the list below, and the principle is now permanent
in `docs/PRODUCT_VISION.md` §6.1.

#### Status, 2026-10-27 — COMPLETE as specified, including the addendum

| criterion | state |
|---|---|
| report renders for the owner on both platforms | **met** — `gabriele_baldocci` (Instagram, 385 publications) and the owner's TikTok (235) |
| and for three competitors of different sizes | **met** — pianza88 (382k followers), tranquilstreamsmusic (32.5k), music.with.hazem (5.4k) |
| no fabricated metric | **met** — every unavailable measure renders as Unavailable with its reason |
| every number traceable by click | **met** — each block carries the publication ids its figures came from, harvested from the block's own body rather than declared beside it |
| four named sections | **met** — Overview, Publication intelligence, Content intelligence, Evidence and uncertainty, plus a plain-English summary ahead of them |
| each conclusion carries its §2 grade | **met** — derived from the block's basis, never written per section |
| contradictory examples listed, not hidden | **met** — the weakest performers beside the strongest, the recent underperformers beside a "recent is better" split |
| paid and organic separated for the owner | **met** — `paid_vs_organic`, Instagram only, stating that Meta's ordinary metrics are already organic-only (ADR 0008) |
| owner's repertoire | **met**, and mostly Unavailable — see below |
| *(addendum)* a non-technical user understands the findings | **met** — six plain questions answered first, no feature name on the first screen |
| *(addendum)* technical detail available but secondary | **met** — every measurement block sits under a closed disclosure |
| *(addendum)* TikTok and Instagram clearly distinguished | **met** — every insight names its platform and refuses to generalise across them |
| *(addendum)* weak evidence and gaps disclosed | **met** — `gaps()` lists all 31 unavailable measurements with reasons |
| *(addendum)* no unproven causal claims | **met**, and tested — a list of causal verbs is asserted absent from every generated sentence |

**What a reader now meets first.** Six questions, answered in English:
how is this creator performing, what appears to work, what appears to work
less well, what is distinctive, what to try next, and how far to trust any
of it. For the owner's Instagram account the second question is answered
from the nine measured associations; for a competitor it answers *"we hold
public counts and no retention data at all, so what works cannot be
answered — only what was posted"*, which is the honest answer and a more
useful one than a confident paragraph built on five numbers.

**Three new layers, each with one job.**

* `server/features/lexicon.py` — plain English for every measurement and
  every outcome, in one place. Display name, what it measures, what high
  and low mean, **what it does not measure**, method, reliability,
  limitations. Centralised because two descriptions of one measurement will
  eventually disagree and one will not.
* `server/research/insights.py` — findings assembled from `feature_evidence`
  rows and the lexicon, and from nothing else. There is no path by which a
  sentence can describe a relationship the database does not hold.
* `server/research/report.py` — the existing report, now grouped, graded,
  linked to its publications and fronted by the summary.

**Four refusals, each enforced by a test rather than a convention.**

1. *No causation.* Sixteen causal phrasings are asserted absent from every
   generated sentence. "Videos with X tended to have Y", never "X improves
   Y".
2. *No correlation as a percentage.* A rho of −0.40 is not "40% fewer
   skips". Strength is a coarse verbal band.
3. *No direction read from a sign.* Skip rate is good when it falls, so
   direction comes from the outcome's own definition. A layer reading
   "negative means bad" would have inverted every finding in the product;
   `saturation_mean` at +0.247 is correctly reported as discouraging and
   `edge_density` at −0.389 as encouraging.
4. *No cross-platform equivalence.* Every insight names its platform, and a
   population with no known platform is withheld rather than shown without
   one.

Plus the one that matters most: **a withheld measurement can never become a
sentence.** A test gives `vision_piano_or_keyboard_visible` a fabricated
rho of −0.7 at q=0 and asserts no insight is produced. It appears as a gap
with its reason instead.

**`text_region_score` is named honestly throughout.** It is "visual
complexity where captions usually sit", it carries an explicit
`not_a_measure_of` saying it does not read, count or detect words, and the
field that actually reads text is shown separately as "Words on screen".
A test asserts the phrase "amount of text" and four variants never appear
in its description.

**Two defects the work surfaced.** Insights initially inherited the
*feature's* grade rather than the grade of the evidence row being described,
so an uncorrected finding was labelled `associated` because a different
finding about the same feature had been corrected — fixed with row-level
grading and a regression test. And the first run rendered Arm A of the
visual-structure analysis as creator-facing findings, producing sentences
like *"videos with more background detail tended to have more visual
complexity in text areas"* — true, internal, and worthless to a creator.
Only audience outcomes become insights now; Arm A stays in the methodology.

**The owner's repertoire is Unavailable, and says so.** 25 publications
trace through the archive to a performance, but `performances.work` is empty
on all 146 rows, so these group publications that share a *performance* and
cannot group them by *piece*. Grouping by repertoire needs that field
populated; the section states that rather than approximating it from titles
or borrowing the caption keyword matches sitting next to it.

**One blueprint assumption corrected by measurement.** M5 says performance
is compared at a fixed age "where ages are known, and labelled *lifetime
approximation* where they are not (every competitor)". Measured: the
discovery provider returns a publication date for **every** competitor
publication held, so competitor ages are known and the lifetime label would
be false. What is genuinely missing is different and is now named
separately — each competitor publication is observed exactly once, at
whatever age it happened to be, so two of them cannot be read at a common
anchor however well their dates are known. The report computes which of the
three statements applies rather than assuming.

**Prepared, not built.** The conversational assistant has its retrieval
surface — `GET /api/features/catalogue`, `/api/features/insights`,
`/api/features/lexicon` — all read-only, all returning evidence with ids to
cite. No chat agent is part of M5, deliberately.

### M6 — Cross-creator intelligence *(old brief Stage 6)*

Comparisons on formats, hooks, repertoire categories, captions, visual
presentation, editing, cadence and relative performance — always with
audience size, data age, completeness and platform visible on the same
screen as the comparison, so a 61-follower account and an 853k one are
never silently averaged. Collections and **control cohorts** are required
before any pattern is graded above Hypothesised across creators
(`docs/SCHEMA_PLAN.md` §3 already requires this).

*Acceptance:* the owner compared against a saved cohort of ≥ 10 creators;
every cross-creator association reported with n and cohort composition.

#### Status, 2026-10-27 — COMPLETE as specified

| criterion | state |
|---|---|
| saved cohort of ≥ 10 creators | **met** — "Classical Piano", 12 TikTok creators, saved as a collection rather than assembled ad hoc |
| the owner compared against it | **met** — `GET /api/features/cohort/{id}`, `#/cohort/2` |
| every association with n and cohort composition | **met** — composition is returned *inside* the comparison, not beside it, so it cannot be rendered without it |
| audience size never silently averaged | **met** — every member's follower count, publications held, publications measured and date range on the same screen |

**The milestone was unreachable when it started, and the first half of
the work was fixing that.** The corpus held 78 competitor publications
across 68 creators — **61 of them with exactly one publication** — 23
publications with any features, 2 creators ever researched, and one
saved collection with zero members. A comparison between the owner's
385 measured publications and somebody's single video is not a
comparison, and no amount of statistics would have made it one.

So M6 collected what M6 needed:

| | before | after |
|---|---|---|
| competitor publications held | 78 | 354 |
| competitor publications measured | 23 | 218 |
| competitor creators measured | 20 | 28 |
| creators with ≥ 5 publications | 1 | 12 |
| saved cohort members | 0 | 12 |

**Cost: $1.60** — $0.843 to collect twelve back catalogues, $0.753 to
acquire and measure 205 videos at the measured $0.00404 a file.
Total spend is now **$2.09 of the $5.00 authorised**. Every file was
deleted after measurement; the audit reports `guarantee_holds: true`
and zero partial extractions across all eleven batches.

**The cohort spans 64 to 2,700,000 followers** — tonyannn at 2.7M down
to cirruspiano at 64 — which is precisely the case the brief names
when it says a 61-follower account and an 853k one must never be
silently averaged. They are not: each member's own median is computed
first and the comparison runs across twelve creators, not across 200
pooled publications. A test builds a cohort where the two give
*opposite* answers and asserts the per-creator one is reported.

**The result, measured.** One difference survives correction:

> On contrast, your openings score higher than this cohort typically
> does. You 63.8 across 225 videos; them 52.8, ranging 28.3 to 71.6
> across 12 creators. Higher than 83% of them. (q = 0.029)

Eight other measurements point the same way and none survives
correction at twelve creators. That is the honest yield of a cohort
this size, and the page says so rather than listing nine findings.

**What this milestone cannot do, stated on the page itself.** No
retention, watch-time or skip data exists for any creator but the
owner, on either platform. Every difference here is a difference in
what the videos *are* and never in what they *achieve*; nothing on
the page can say a difference is why anyone performs better. Every
finding is therefore graded **Observed** and the ladder in
`features/catalogue.py` has no route for it to go higher — a
cross-creator difference has no outcome to be tested against. A test
asserts that nothing can reach Associated here however small its q.

**An unexplained gap in the audit trail, recorded rather than
smoothed over.** 40 `transient_media` rows from the M3 run are
recorded as `crash_recovery` — the reaper found the files already
absent and reconciled the rows. Nothing crashed, and what deleted
those files between acquisition and the next sweep has not been
identified. The retention guarantee held throughout and no file
outlived its deadline, so this is a provenance defect rather than a
safety one, but the reason recorded against those forty rows is not
the true reason. Tracked in `DEVELOPMENT_PLAN.md` §11.

### M7 — Creative Opportunities *(old brief Stage 7)*

Each opportunity is a structured object: proposed approach, motivating
pattern (with grade), supporting examples, counterexamples, evidence
strength, limitations, a concrete experiment, and the metrics to collect.
Generated from the registry and the report, not from a model's opinion.
Personalised from the owner's actual repertoire, library and history.
Read-only, and designed so that an opportunity can later become a
pre-registered experiment (M9) with one click.

*Acceptance:* ≥ 5 opportunities generated from real data, each one
traceable to specific publications; none graded above the evidence behind
it.

#### Status, 2026-10-28 — COMPLETE as specified

| criterion | state |
|---|---|
| ≥ 5 opportunities from real data | **met** — 10, from 9 corrected associations and 1 surviving cohort difference. No model was asked anything; they are assembled from `feature_evidence` and `cohort.compare`. |
| each traceable to specific publications | **met** — 10 of 10 carry real publication ids with working permalinks: 72 clickable links on the page, verified in headless Chromium |
| none graded above its evidence | **met** — 9 Associated, 1 Observed. The cohort-derived one is capped at Observed by construction and a test asserts it cannot rise |
| read-only, pre-registerable later | **met** — `GET /api/opportunities` only; the experiment object already carries change, comparison, stopping rule, metrics and sample size |

**The milestone found something that changes what the product should
say.** The "suggested experiment" M5 printed beside each finding was
unrunnable. The owner posts **4.84 times a month** on Instagram (386
publications over 2391 days, measured). Detecting a correlation of the
size actually observed needs **69 to 1432 publications** depending on
the finding — between 14 months and **296 months**. "Post a run of
videos with more background detail and compare" was a twenty-five-year
commitment described as an afternoon's work.

So every opportunity now carries the number of publications its
experiment needs, how long that is at the rate the owner actually
posts, and a verdict: `practical`, `slow` or `impractical`. They are
**ordered by how quickly they could be answered**, not by evidence
strength — ranking by q-value would put a two-year commitment above a
four-month one, which is the wrong answer to "what should I do next".

**A second design is offered where it helps, and it helps a lot.**
Re-detecting a correlation means waiting for the natural spread of
what gets posted anyway. Deliberately alternating clearly-high and
clearly-low openings samples the ends of the distribution instead, and
needs far fewer publications:

| opportunity | wait for the correlation | deliberately contrast |
|---|---|---|
| overall visual detail | 69 publications, 15 months | **22, 5 months** |
| background visual detail | 76, 16 months | **24, 5 months** |
| background tonal variety | 108, 23 months | **34, 7 months** |
| colour saturation | 117, 25 months | **38, 8 months** |
| on-screen text area | 126, 26 months | **40, 9 months** |

That turns **0 of 10 practical into 5 of 10**. The trade is stated on
every card: it answers a question about the extremes rather than about
the range normally posted, and it is only cheaper if the contrast is
actually struck.

**Counterexamples are mandatory and shown beside the supporting ones.**
A correlation of 0.3 means most publications do not follow the line,
and a page listing only the publications that fit would be literally
true and systematically misleading. Each opportunity shows four of
each, with links to the real posts.

**Two bugs worth recording.** The first draft guessed
`hook-deterministic-v1` as the method version and produced *zero*
examples for every `background_*` feature, which lives under
`hook-background-v1` — a silent, total failure that looked like
missing data; the catalogue is now asked. The second rendered a 77.6%
skip rate as **"7760%"**, because `skip_rate` is stored 0–100 and was
formatted with `%`. Both are now tests.

### M8 — The Strategist

The chat the owner asked for. The owner has said a powerful external model
is acceptable. The design makes that safe:

- **The model never sees the database.** It sees an *evidence context*
  assembled by ViralLab: the registry, the report sections, the
  opportunities, the owner's library — every item carrying its grade and
  its source ids.
- **Tools, not memory.** The model answers numeric questions by calling
  read-only tools (`creator_report`, `compare`, `publication`,
  `feature_lookup`, `evidence_for`) and must cite the tool result. A number
  with no tool citation is rendered with a warning and logged.
- **Refusal rules in the system prompt and enforced in code**: no causal
  language for anything below Validated; no virality promises; withheld
  features answered as Unavailable; no identification of people from
  frames.
- **Provider through the existing AI layer** under the `strategy` category
  cap, which is currently $0.00 and stays there until the owner sets it in
  the dashboard. Competitor media never leaves the server; derived features
  and public metadata do, and that is recorded.
- Every conversation is logged with the evidence context it was given, so a
  wrong answer can be traced to either the evidence or the model.

*Acceptance:* ten scripted questions answered with correct citations; three
trap questions ("will this go viral?", "is the piano visible in video X?"
where X is withheld, "why did video Y succeed?") refused correctly.

#### Status, 2026-10-28 — COMPLETE as specified

| criterion | state |
|---|---|
| ten questions, correct citations | **met** — 10 of 10 called a tool, cited it, and stated no figure the tools had not returned. Total cost 6.2 cents |
| three traps refused | **met** — all three, and all three refused *before any model was called*, so they cost nothing |
| model never sees the database | **met** — five read-only tools and an ids-only context block. No SQL path, no schema, no table names |
| refusal rules enforced in code | **met** — `review()` reads the finished text; the prompt states the rules because that makes compliance likely, not because it enforces them |
| every conversation logged with its evidence | **met** — `strategist_turns` stores the tool results beside the answer, so a wrong figure is attributable to the tool or the model |
| `strategy` cap ships at $0.00 | **met** — opened to $3.00 only on the owner's explicit authorisation, separately from building it |

**The guarantee that does not depend on the model cooperating** is the
number check. Every figure in a finished answer is matched against
what the tools actually returned; anything unmatched is flagged in the
log and marked in the text where the reader meets it. A rounded
restatement of a returned value passes, a year or a small count is not
treated as a claim, and an invented figure does not get through.

**The first live run scored 6 of 10, and the four failures were all
mine.** Running it is what found them:

| failure | cause | fix |
|---|---|---|
| "no measurement called edge density" | the model types `edge density`, the column holds `edge_density` | names resolved, with suggestions on a miss |
| "no evidence for saturation_mean" — a relationship that exists | the model passed outcome `skip rate`, not `reels_skip_rate` | outcome names resolved too |
| two questions called no tool and asked for a collection id | nothing had told it collections have ids | an ids-and-names-only context block |
| **"is sheet music visible in 287?" was refused** | the withheld gate fired on an `indicative` field | narrowed to `unverified` only |

That fourth one is the one worth remembering. Refusing it was the
enforcement being *wrong*, not strict: a description question about
one video, answerable from data that exists. An assistant that
refuses answerable questions teaches the owner to stop asking, and
over-blocking is a failure mode as real as over-claiming.

Two further bugs died in the same run: `cohort_median` does not exist
(the attribute is `cohort_median_of_medians`, because M6 refuses to
pool), and tool output containing dates crashed the JSONB write
*after* the model had been called and billed.

**Known limitation, recorded rather than smoothed over.** The causal
matcher exempts a causal verb near a negation, because the correct
answer here says "this is not evidence of cause" constantly and
blocking those would leave the assistant unable to state its own
limits. A sentence that negates one thing and asserts cause about
another inside the same window slips through. A test documents it.

### M9 — The experiment loop *(existing Phase 9, now with the ad in it)*

Discover → Analyse → Compare → Recommend → **Create → Publish → Measure →
Learn.** Pre-registration before publishing; the stopping rule honoured;
age-anchored measurement; and the **ad held constant or randomised across
arms**, otherwise the experiment measures the ad. Results feed grades back
into the registry: an Associated pattern that survives becomes Validated;
one that fails is marked so and the opportunity that proposed it is
retired. This is the only place a causal claim can be born.

*Acceptance:* one completed pre-registered experiment with its outcome
recorded against its prediction, whichever way it went.

#### Status, 2026-10-28 — BUILT AND RUNNING; acceptance NOT YET MET

The loop is complete and one experiment is **locked and running**.
The acceptance criterion says *completed*, and it is not, because
completing it requires posting 22 publications. At the owner's
measured rate of 4.84 a month that is **about four and a half
months**. No amount of engineering shortens it, and anything that
appeared to would not be an experiment.

**Experiment 1, pre-registered 2026-10-28:**

> Publications whose openings carry deliberately more overall visual
> detail will show a **lower** reels skip rate at 7 days than
> publications in the other arm. If the difference is in the other
> direction, or is not distinguishable from none, the prediction is
> wrong.

11 per arm, 22 slots, arms drawn at lock time in balanced pairs
(`1T 2C 3T 4C 5T 6C 7T 8C 9C 10T 11C 12T …`), ad held constant,
Mann-Whitney U at α = 0.05, read once. It came from the strongest
practical M7 opportunity in one click, and its sample size is that
opportunity's own power calculation rather than a number typed into
a box after seeing how long it would take.

| criterion | state |
|---|---|
| pre-registration before publishing | **met** — prediction, n, test, anchor and stopping rule frozen at lock; a test asserts a moved field voids the run |
| the stopping rule honoured | **met** — `read_out` raises rather than returning a partial answer. It currently refuses with *"22 of 22 publications still to post"* |
| age-anchored measurement | **met** — a 7-day anchor is read at day 9, and immature publications are refused with the reason |
| the ad held constant or randomised | **met** — recorded in the design and checked at readout; a contaminated run raises rather than being reported with a caveat |
| grades fed back | **met** — supported ⇒ Validated, failed ⇒ Observed and the opportunity retired, inconclusive ⇒ unchanged |
| **one completed experiment** | **not met** — ~4.5 months of posting away |

**The readout is tested even though it will not run for months.** A
verdict that first executes in the one moment it matters is a verdict
nobody has checked, so it is driven end to end against publications
built to order: an arm that wins, an arm that loses, and two that
cannot be told apart. The middle case is the one worth the fixture —
a significant result in the *wrong* direction is `not_supported`, not
`inconclusive`, and burying it would hide the most interesting thing
an experiment can produce.

**This is the only route to `Validated` in the system**, and a test
scans the whole server package to prove no other code path assigns
that grade. It is what lets `strategist.py` enforce "nothing causes
anything" as an absolute today and have the gate open by itself when
this loop first produces one.

**Two bugs found by running it rather than reading it.** The
opportunity's `direction` says which way to move the *feature*; the
prediction is about the *outcome*, and conflating them would have
registered the opposite of the finding with no test noticing, because
both are valid strings. And the ad check counted any non-zero
covariate as a boost, flagging every publication in a run with no ads
at all — the module has its own `BOOSTED_SHARE_THRESHOLD` and
inventing a second notion of "boosted" was the error.

### M10 — The creative suite *(post-MVP, each item graded)*

Capabilities worth building once M1–M9 exist, with the evidence each
depends on:

| capability | depends on | grade it can reach |
|---|---|---|
| Hook library — own and competitor openings, searchable by feature | M2, M4 | Observed |
| Repertoire planner — which pieces and categories performed, by platform | M1, `performances` | Associated |
| Format and trend tracker — formats per niche over time, breakthrough alerts | M2, M6 | Observed / Hypothesised |
| Cadence analysis — posting rhythm vs outcomes, ad-corrected | M1 | Associated |
| Pre-publish scorecard — a checklist of Validated and Associated features present in a draft, **never a score** | M3, M4, M9 | reflects grades |
| Caption and overlay assistant — grounded in the owner's own winners | M4, M8 | Hypothesised |
| Shot-list and storyboard assistant — from promoted visual features only | M3 | reflects grades |
| Audio structure assistant — climax timing, dynamics, onset | M4 (tempo/climax) | Associated |
| Trend-sound intelligence — TikTok `music` metadata across the cohort | M2 | Observed |
| Weekly intelligence brief — automated, graded, with what changed | M5, M6 | reflects grades |
| Content calendar with experiment slots | M9 | utility |
| Multi-profile and agency mode | legal review first (ADR 0007 §Terms) | — |
| Publishing integration | Phase 12 in the existing plan; after legal review | — |

What will not be built: a black-box virality predictor, a score that
implies a probability of success, or any recommendation whose grade is
below the claim it makes.

---

## 4. Schema additions

| table / column | purpose | milestone |
|---|---|---|
| `metrics_snapshots.paid_*`, `organic_*` | the separation, never overwriting raw | M1 |
| `ad_exposures (video_id, ad_id, spend_usd, impressions, reach, …)` | one row per ad per post | M1 |
| `analytics_imports` + `imported_metrics` | manual exports with provenance | M1 |
| `transient_media (video_id, path, acquired_at, expires_at, source, permission_basis, deleted_at, audit)` | the six-hour ledger | M2 |
| `feature_registry` | the catalogue with grades | M4 |
| `evidence_claims (grade, statement, supporting_ids, contradicting_ids)` | every report statement | M5 |
| `opportunities`, `experiments`, `experiment_arms`, `preregistrations` | M7 / M9 | M7, M9 |
| `strategist_sessions`, `strategist_turns (evidence_context, citations)` | auditability | M8 |

All through Alembic, each validated empty → head → downgrade → head on the
scratch database before production, as every migration so far has been.

---

## 5. Costs, measured and projected

| item | figure | tag |
|---|---|---|
| Apify TikTok, per item | $0.0003 | `[PRIMARY]` |
| Apify Instagram hashtag, 30 items | $0.078 | `[PRIMARY]` |
| Apify month to date | $0.4605 of $5.00 | `[PRIMARY]` |
| Free plan item cap per TikTok run | 10 | `[PRIMARY]` |
| Local vision | $0; 7.9 s/video; 4034 MiB | `[PRIMARY]` |
| Competitor media via provider add-on | per-event price not exposed to a free account | `[OPEN]` |
| Cloud vision for M3 labelling, 80 videos × 6 frames | provider-dependent, order of $1–5 | `[ASSUMPTION]` |
| Strategist, external model | per conversation, provider-dependent; capped by `strategy` | `[ASSUMPTION]` |

A paid Apify plan removes the 10-item cap and is the single biggest
accelerator for the competitor corpus. That is the owner's call and is not
assumed.

---

## 6. Questions for the owner

1. When did the $1 ad start, and has the spend or targeting changed since?
2. Has TikTok ever been boosted? (If not, it is the control.)
3. Can the Facebook Login token be granted `ads_read`, or can Ads Manager
   access be shared, so paid metrics can be read by the API?
4. Which external model provider for the Strategist, and what monthly cap?
5. Is a paid Apify plan acceptable, and at what cap?
6. Are own-account metrics acceptable to send to an external model? (Media
   never is; metrics and derived features would be.)

---

## 7. Sequencing

M1 first, because every later milestone consumes the outcome variable M1
cleans. M2 and M3 can run in parallel — one is plumbing, the other is
measurement. M4 consolidates them. M5–M7 are the product, in that order,
each read-only. M8 sits on top of M7 and must not precede it, or the
strategist has nothing graded to say. M9 is where causal claims become
possible. M10 is a menu, not a sequence.

The MVP still ends where CLAUDE.md §8 says it ends: with the experiment.
A suite that recommends without having tested one recommendation is the
failure mode the project was designed to avoid.

## 8. The parameter catalogue — what could distinguish a video that
worked from a similar one that did not

Written 2026-10-28, at the owner's request, after he observed that
the build had drifted from what he needed. This is the target list.
It is deliberately larger than what exists, and it marks honestly
which rows are real.

**Key:** ✅ built · ◐ partial · ○ buildable free from data already
held · £ needs media or a provider · ✗ not obtainable

---

### 8.0 The framing, which the owner's phrasing got right

The request was for what distinguishes a video that worked "with
respect to another which is similar and draws no traffic". That
phrasing contains the better instrument, and it is not a longer
parameter list.

A correlation across a corpus asks *do busier openings go with more
views, on average, across everything*. It is answerable and it is
weak, because everything differs from everything at once. **A matched
pair asks a sharper question**: here are two videos by the same
creator, same repertoire, similar length, posted three weeks apart,
one at the top of their distribution and one at the bottom — what
differs?

Within a pair, the creator is constant, the audience is constant, the
follower count is nearly constant, the era is constant, and often the
repertoire is too. Every confound that makes cross-creator comparison
nearly worthless is differenced out by construction rather than by
statistics.

**So the parameters below exist to populate pairs, not to be
correlated one at a time.** The design is in §8.9 and the list should
be read with it in mind: a parameter earns its place by being capable
of *differing within a matched pair*, not by sounding important.

---

### 8.1 The opening — the first three seconds

The part with the most leverage per byte, because it is where a
viewer decides. Most of the existing feature set is already here.

**Visual composition**

| parameter | status |
|---|---|
| What is on screen at t=0 — face, hands, keys, text card, instrument, empty | ◐ `vision_*` booleans, not time-resolved to t=0 |
| Share of the frame the face occupies | ○ |
| Share of the frame the subject occupies | ◐ `background_subject_edge_ratio` |
| Shot distance, camera angle, framing | ✅ `vision_shot_distance`, `vision_camera_angle`, `vision_framing` |
| Depth of field, background blur | ✅ `vision_depth_of_field` |
| Brightness, contrast, saturation | ✅ `brightness_mean`, `contrast_rms`, `saturation_mean` |
| Colour temperature and dominant hue | ○ |
| Visual busyness, overall and background | ✅ `edge_density`, `background_edge_density` |
| Background tonal variety | ✅ `background_grey_entropy` |
| Negative space, symmetry, thirds adherence | ○ |
| Motion energy in the first second | ✅ `mean_abs_frame_diff` |
| Camera movement and its magnitude | ✅ `camera_motion_class`, `camera_motion_px_per_frame` |
| Time of the first cut | ◐ `cut_times_s` holds it; not surfaced |
| Gaze direction — at camera, at keys, away | ○ via the wide pass |
| Letterboxing or pillarboxing | ○ |
| Motion blur | ○ |

**Audio opening**

| parameter | status |
|---|---|
| Silence before the first sound | ✅ `first_sound_at_s` |
| Whether sound starts immediately | ✅ `audio_starts_immediately` |
| Loudness of the first seconds | ✅ `audio_rms_mean`, `audio_rms_peak` |
| Attack sharpness of the first note | ✅ `audio_attack_time_s` |
| Brightness of the sound | ✅ `spectral_centroid_hz` |
| Speech first or music first | ◐ `voice_band_ratio`, `spoken_words` |
| Tempo at the start | ○ |
| Key and mode — major or minor | ○ |
| Dynamic range of the opening | ○ |
| Note density — how many events per second | ◐ `audio_onset_count` |
| Solo or ensemble | ○ via the wide pass |
| Whether the melody is recognisable within 3 seconds | ✗ needs a melody index this project does not have |

**On-screen text at the opening**

| parameter | status |
|---|---|
| Whether text is present, and when it first appears | ✅ `vision_text_overlay_present`, `ocr_first_text_at_s` |
| Word and element count | ✅ `ocr_word_count`, `ocr_element_count` |
| Where on screen it sits | ✅ `text_region_score`, `vision_text_position` |
| Text height as a share of the frame | ○ |
| Contrast of text against what is behind it | ○ |
| Whether it is a question, a claim, a number or a promise | ○ from `on_screen_text_content` |
| Reading time against display time | ○ |
| Language | ○ |
| Burned in by the creator or added by the platform | ✅ `vision_text_overlay_present` makes this distinction |

---

### 8.2 Structure and pacing

| parameter | status |
|---|---|
| Duration | ✅ `duration_s` |
| Number of cuts and cut rate | ✅ `cut_count` |
| Regularity of the cutting rhythm | ○ from `cut_times_s` |
| Longest single shot | ○ from `cut_times_s` |
| Scene changes | ✅ `scene_change_count` |
| Whether it loops seamlessly — last frame against first | ○ |
| Whether it ends abruptly or resolves | ○ |
| Where the loudest moment sits | ○ |
| Whether it builds to a peak or is flat | ○ |
| Speed ramps and slow motion | ○ via the wide pass |
| Transition types | ✅ `vision_transitions` |
| Whether the opening represents the rest | ✅ `vision_opening_is_representative` |

---

### 8.3 Repertoire and content

The dimension the owner named first and the one with the least built.
A concert pianist's catalogue is not interchangeable content.

| parameter | status |
|---|---|
| Composer | ✅ `composer` from captions, 450 publications |
| Work and movement | ◐ `work_form_named` only — the form, not which piece |
| Period — baroque, classical, romantic, impressionist, modern | ○ derivable from composer |
| Recognisability — is this a piece a lay listener knows | ○ needs a curated tier list |
| Whether it is a "meme" piece — Für Elise, Nocturne op.9 no.2, Interstellar | ○ curated list |
| Perceived virtuosity — speed, volume, note density | ◐ from audio |
| Tempo | ○ |
| Mode — major or minor | ○ |
| Original composition against repertoire | ○ |
| Arrangement against straight performance | ○ |
| Crossover — classical meeting pop | ○ |
| Participation in a trend or challenge | £ needs trend data |
| Content type — performance, tutorial, talking head, reaction, behind the scenes, comparison, storytime | ○ via the wide pass |
| Whether it teaches something | ○ |
| Whether it is funny | ○ interpretive, never verifiable |

---

### 8.4 Performer and setting

| parameter | status |
|---|---|
| Performer visible, and how many | ✅ `vision_performer_visible` (withheld), `vision_second_person_visible` |
| Face visible, facing the camera | ✅ `vision_face_visible`, `vision_facing_camera` |
| Expression | ◐ `vision_emotional_presentation`, interpretive |
| Amount of body movement | ○ |
| Dress formality | ✅ `vision_formality` |
| Setting | ✅ `vision_setting` |
| Audience visible, and how large | ✅ `vision_audience_visible` |
| Instrument type — grand, upright, digital, public piano | ○ via the wide pass |
| Production values — lighting, stability, resolution | ◐ |
| Handheld or mounted | ✅ `camera_motion_class` |
| Props — candles, flowers, chandelier, a screen | ✅ four `vision_*` booleans |
| A pet or a child in shot | ✅ `vision_pet_or_child_visible` |

---

### 8.5 Caption and metadata

| parameter | status |
|---|---|
| Length in characters and words | ✅ `caption_chars`, `caption_words` |
| Length of the first line — what shows before "more" | ✅ `caption_first_line_words` |
| Hashtag count | ✅ `hashtag_count` |
| Hashtag popularity, and broad against niche | £ needs per-hashtag volume |
| Mentions and collaborations | ✅ `mention_count` |
| Emoji count | ✅ `caption_emoji_count` |
| Asks a question | ✅ `caption_has_question` |
| Asks the viewer to act | ✅ `caption_has_cta` |
| Names the piece | ✅ `composer_named`, `work_form_named` |
| Tells a story against labelling the content | ○ |
| Sentiment | ○ |
| Language | ○ |
| Hashtags and nothing else | ✅ `caption_is_hashtags_only` |

---

### 8.6 Sound identity

Platform-level, and entirely missing. On TikTok especially, which
sound a video carries is a distribution mechanism in its own right.

| parameter | status |
|---|---|
| Original audio against a trending sound | £ |
| Sound identifier | £ |
| How many other videos use that sound | £ |
| Whether the sound was rising at the time of posting | £ |

---

### 8.7 Timing and context

| parameter | status |
|---|---|
| Hour of day, UTC | ✅ `posted_hour_utc` |
| Hour of day in the creator's local time | ○ needs a timezone per creator |
| Day of week, and weekend | ✅ `posted_weekday`, `posted_is_weekend` |
| Days since the previous post | ○ |
| Regularity of posting cadence | ○ |
| Follower count at the time of posting | ◐ only the current count is held |
| Account age at the time of posting | ○ |
| Part of a series | ○ |
| Season, and proximity to a holiday | ○ |
| How many similar videos were posted that week | £ |

---

### 8.8 Novelty against the creator's own catalogue

A dimension nothing in the project touches, and plausibly one of the
strongest: audiences reward difference from what an account usually
does, and the same video from a different account is a different
event.

| parameter | status |
|---|---|
| Visual similarity to the creator's recent posts | ○ |
| Audio similarity to recent posts | ○ |
| Whether this repertoire has been posted before | ○ |
| Whether this format has been posted before | ○ |
| A combined novelty score | ○ |

---

### 8.9 Outcomes — never predictors

CLAUDE.md §3.9 forbids a post-publication signal from being both a
feature and a target. These are targets.

| outcome | owner | other creators |
|---|---|---|
| Views | ✅ | ✅ 1,061 snapshots |
| Likes, comments, shares, saves | ✅ | ✅ |
| Skip rate | ✅ 287 publications | ✗ private to the account holder |
| Average watch time | ✅ | ✗ |
| Completion and rewatch rate | ◐ | ✗ |
| Follows gained from a video | ◐ | ✗ |
| Share-to-view ratio — the strongest "this struck someone" signal | ○ | ○ |
| Comment sentiment and volume | ○ | ○ |

---

### 8.10 What cannot be measured, and matters

Listing these is not pessimism. A model that silently omits them
attributes their effect to whatever it did measure.

- **The algorithm's state.** Which audience a video was shown to, how
  large the first test cohort was, and what it did there. This is
  invisible and it is probably the largest single term.
- **Early-velocity cascades.** Short-form distribution is
  path-dependent: two near-identical videos can diverge because one
  caught a better first hour. A meaningful share of outcome variance
  is therefore **not attributable to content at all**, and a system
  that explains every difference by content is overfitting to noise.
- **Off-platform promotion.** A share in a group chat or a feature by
  a larger account.
- **Comment-section dynamics.** A good argument in the replies keeps
  a video alive for days.
- **The creator's standing relationship with their audience.**
- **Paid promotion on other creators' accounts.** Nobody can see it,
  which is precisely why no finding about other creators can rise
  above `Observed` — there is no ad covariate to control for.

---

### 8.10a Breakthroughs are a threshold, not the top of a gradient

Added 2026-10-28 after the owner pointed out that the breakthrough
is always one of the most important things to consider. He was
right, and the correction is methodological rather than cosmetic.

A correlation against view rank treats performance as a smooth
gradient: it asks what nudges the 50th percentile to the 60th. But
breaking out is not the top of that gradient. A video at ten times
its creator's median is a *different kind of event* from one at 1.2
times, and what separates those two need not be what separates
average from slightly-above-average.

So every measurement is also asked the threshold question: take
publications that beat their own creator's **rolling local
baseline** by a wide margin, and compare them with everything that
did not.

The rolling baseline is why this rests on firmer ground than the
percentile version. `baseline.rolling_baseline` scores each
publication against the window that *preceded* it - trailing, not
centred - so age is handled by construction. An old post is compared
with its contemporaries rather than with the creator's whole career,
instead of having a log partialled out of it and hoping.

**It worked immediately, and on the side that had nothing.** The
gradient analysis found zero competitor findings across 48
measurements. The threshold question found one: breakthrough videos
carry **larger on-screen text** than their creator's other
publications, 0.065 against 0.048 of frame height, across 34
outliers of 351 scored. That is the first thing this project has
established about other people's videos.

On the owner's own account, 97 outliers of 560, four differences
survive - more visual detail, more background detail, more
complexity where captions sit, and *less* colour intensity - and all
four also appear in the gradient analysis, which is a consistency
check passing rather than a second finding.

**Two cautions that belong next to it.** A breakthrough is also the
outcome most likely to be luck, because §8.10's cascade dynamics
compound hardest at the top. And these are what outliers had in
common, not what made them outliers - the difference between those
two sentences is the whole of §8.9.

### 8.11 The matched-pair design

The instrument the owner's question actually implies.

**Pairing rule, to be fixed before any pair is drawn.** Two
publications form a pair when they share a creator, are within a
stated window of each other in posting date, are within a stated
ratio on duration, and ideally share a composer or content type —
and when one sits in the top third of that creator's view
distribution and the other in the bottom third.

**Then test which parameters differ systematically across pairs**, by
sign test or Wilcoxon on the within-pair differences. A parameter
that differs in the same direction in 18 of 22 pairs is saying
something a pooled correlation of 0.15 cannot.

**What it buys.** Creator, audience, follower count, era and often
repertoire are all held constant by construction. It is the closest
thing to an experiment that observation allows.

**What it does not buy.** It is still observational. Pairs differ on
many parameters at once, and the one that differs most is not
thereby the cause. And it inherits §8.10 entirely: a pair may differ
only in which hour the algorithm happened to test it.

**Why it is not built yet.** It needs depth per creator. 16
competitor creators with eight or more measured videos is not enough
to draw pairs from; roughly 100 creators with 30 or more each would
be. That is the binding constraint on this whole section, and it is a
collection problem rather than an analysis one.

---

### 8.12 What to build first

Ordered by evidence gained per pound, not by interest.

1. **Everything marked ○ that needs no media** — tempo, mode,
   recognisability tiers, period from composer, novelty against the
   creator's own catalogue, days since last post, text classification
   from OCR already stored. Free, and it roughly doubles the
   parameter count on both sides of the comparison.
2. **Share-to-view ratio as an outcome.** Already collectable, and a
   better signal of "this struck someone" than raw views.
3. **Depth over breadth in collection.** 100 creators × 30
   publications beats 400 creators × 2, and only the first supports
   matched pairs.
4. **Sound identity**, if the provider exposes it. On TikTok it may
   be a larger term than anything visual.
5. **The visual pass on competitors.** The most expensive, and worth
   less than the above until there are pairs to use it in.
