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
