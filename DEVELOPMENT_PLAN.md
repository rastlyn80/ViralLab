# ViralLab — Development Plan

**Revision 3** — Phases 0A, 0B and 1 complete (2026-10-24)
Status: **0A GATE PASSED** (incl. ToS review) · **0B GATE PASSED** ·
**PHASE 1 COMPLETE** — 28 automated tests passing on both machines, 11/11
cross-machine acceptance checks. **Phase 2 in progress.**
Owner: Gabriele Baldocci (private use initially; architecture kept niche-agnostic for future reuse)
Last updated: 2026-10-24

> **Revision 3 changed:** added the fixed-age metric comparison requirement
> (§5.4) and its consequences for discovery design; expanded the 0B cost
> benchmark to total end-to-end cost including model-loading overhead,
> downloads, storage and Tier 3 (§7 Phase 0B, §10); and introduced explicit
> **[PRIMARY]** / **[SECONDARY]** source tagging on every factual claim
> (§2), which upgraded four claims to primary sources and surfaced three new
> findings — Instagram's 2-year insights retention limit, its up-to-48-hour
> metric delay, and the pre-July-2024 `impressions` exception.
>
> *Revision 2 had:* added the gated feasibility phases (§7 Phase 0A/0B),
> moved local AI benchmarking to the front, introduced three-tier
> progressive analysis with stratified sampling (§5.2), brought a live
> own-account experiment into the MVP (§7 Phase 8), strengthened predictive
> validation to five named tests (§12.3), hardened the job system (§4.2),
> and corrected three API errors (§2.1).

### Source-tagging convention (Revision 3)

| Tag | Meaning |
|---|---|
| **[PRIMARY]** | Verified against the platform's own official developer documentation, changelog, or a court record. Citable as fact. |
| **[SECONDARY]** | Supported only by third-party documentation, vendor pricing pages, or community benchmarks. **Treat as provisional**; re-verify against a primary source before relying on it. |
| **[ASSUMPTION]** | Engineering judgment, not measured. Must be replaced by measurement. |
| **[OPEN]** | Unresolved; tracked in §11. |

Revision 1 of this plan was wrong on three points precisely because
secondary sources were treated as primary. The tags exist to make that
failure mode visible rather than repeatable.

---

## 1. Project Overview

ViralLab is a local-first system that collects performance data on short-form
video content (TikTok, Instagram Reels; YouTube Shorts later), extracts
structured multimodal features from the videos themselves, and builds
statistical/ML models that relate those features to relative performance
within a creator's own history and niche. The output is evidence-tagged,
specific content recommendations for the owner's own accounts, plus a
measured test of whether those recommendations actually improve real
outcomes.

ViralLab **does not** claim to reverse-engineer platform ranking algorithms
and **does not** guarantee virality. Every recommendation must be traceable
to either (a) a measured statistical association in the collected data, or
(b) an explicitly labeled hypothesis. Those two categories are never allowed
to blur together in the UI, logs, or stored recommendation records.

The system is built for two home machines (always-on Ubuntu server,
occasionally-on Windows GPU box) and is designed so the Windows machine can
disappear for days without anything breaking or being lost.

---

## 2. Feasibility Findings

Each item is marked **[VERIFIED]** (confirmed against current official
documentation or case law), **[ASSUMPTION]** (engineering judgment, not yet
measured), or **[OPEN]** (unresolved; tracked in §11).

### 2.1 Data access — your own accounts

**Corrections from Revision 1.** Three claims in Revision 1 were sourced
from third-party blogs and were wrong or incomplete. All three are corrected
below and the mistake is the reason §9 of CLAUDE.md now mandates
official-doc verification.

- **[PRIMARY — CORRECTION]** **Instagram deprecated `plays`,
  `clips_replays_count`, `ig_reels_aggregated_all_plays_count` and
  `impressions` on 21 April 2025**, confirmed in Meta's own Graph API
  changelog, replaced by a unified **`views`** metric (introduced as the
  primary consumption metric in August 2024). Revision 1 listed
  "impressions, plays" as available — stale by eighteen months.
  Per Meta's official Instagram Media Insights reference, the metric set to
  target is **`views`** (total times the media was played), **`reach`**
  (unique accounts), **`saved`**, **`shares`**, plus likes/comments.
  ([Graph API v22.0 changelog](https://developers.facebook.com/docs/graph-api/changelog/version22.0/),
  [Instagram Media Insights reference](https://developers.facebook.com/documentation/instagram-platform/reference/instagram-media/insights))

- **[PRIMARY — NUANCE]** `impressions` is deprecated **only for media
  created on or after 2 July 2024**; for older media the API continues to
  return it across all versions. Since your archive likely predates that
  date, `impressions` may still be available for your historical videos —
  but it will be **absent for everything new**, so it cannot be used as a
  modeling feature. Collect it as historical context only, never as a
  model input.

- **[PRIMARY — NEW FINDING, affects Phase 2 scope]** Instagram
  **insights data is retained for only ~2 years**, and metric values can be
  **delayed by up to 48 hours**. Two consequences:
  1. **Own-account backfill is capped at ~2 years.** Any video older than
     that has metadata but no recoverable insights. If your archive runs
     deeper, that history is permanently unavailable via the API — a reason
     to start collecting now rather than later, since the window is
     rolling.
  2. **The 48-hour delay interacts with fixed-age comparison (§5.4):** a
     "7-day" metric must be read at **day 9 or later**, not exactly day 7,
     or it may be truncated.
  ([Instagram Media Insights reference](https://developers.facebook.com/documentation/instagram-platform/reference/instagram-media/insights))

- **[PRIMARY — CORRECTION]** There are **two distinct Instagram API paths**,
  and they are not feature-equivalent:
  | | **Instagram Login** | **Facebook Login** |
  |---|---|---|
  | Host | `graph.instagram.com` | `graph.facebook.com` |
  | Facebook Page required | No | **Yes** |
  | Token | IG User token (1h → 60-day, refreshable) | FB User token |
  | Hashtag search | **Not available** | Available |
  | Business Discovery (other accounts) | **Not available** | Available |

  Both paths require a Professional (Business or Creator) account; personal
  accounts have **zero** API access.
  → **Decision: use the Facebook Login path.** Hashtag search and Business
  Discovery are the only official-API footholds ViralLab has for anything
  beyond its own account, and they exist solely on that path. The extra
  Facebook Page requirement is a one-time setup cost worth paying.
  ([Instagram Platform overview](https://developers.facebook.com/docs/instagram-platform/overview/))

- **[PRIMARY — MAJOR CORRECTION] The TikTok Display API does not return a
  downloadable video file.** The official Video Object fields are: `id`,
  `create_time`, `cover_image_url`, `share_url`, `video_description`,
  `duration`, `height`, `width`, `title`, `embed_html`, `embed_link`,
  `like_count`, `comment_count`, `share_count`, `view_count`, `is_aigc`.
  There is **no media URL** — only an embed/share link intended for a web
  view. Note also that **`collect_count` (saves) is not in this list**;
  Revision 1 claimed saves "in some responses," which was unsupported.
  ([TikTok Video Object](https://developers.tiktok.com/docs/en/tiktok-api-v2-video-object),
  [List Videos](https://developers.tiktok.com/docs/en/tiktok-api-v1-video-list))

- **[SECONDARY]** By contrast, **Instagram's Graph API does expose
  `media_url`** for your own media — a signed CDN link that **expires
  (~24h per most sources, ~105h per one)**. Media must therefore be
  downloaded promptly and re-hosted locally; a stored `media_url` is
  worthless a day later. *Exact TTL is not documented in Meta's primary
  reference — treat the number as provisional and measure it empirically in
  Phase 2 rather than relying on any quoted figure.*

**The metadata-vs-media asymmetry (new architectural consequence):**

| | Metadata + metrics | Downloadable media |
|---|---|---|
| TikTok (own account) | ✅ Display API | ❌ **none via official API** |
| Instagram (own account) | ✅ Graph API | ✅ `media_url`, expiring — download immediately |
| Third-party / niche videos | via data provider (§2.4) | via data provider, legally weightier |

→ **Design addition: a local media-ingest path.** You are the creator of
your own content, so you hold the original master files. Rather than
scraping your own TikToks back off the platform, the system takes a
**watched folder** on the Ubuntu server where you drop masters; an ingest
job fingerprints them (duration + perceptual hash of the cover frame) and
links them to the API-fetched metadata row. This is both the cleanest
legal path and the highest-quality source (masters, not re-compressed
platform copies). Manual linking via the dashboard covers any fingerprint
misses.

- **[SECONDARY]** TikTok Display API rate limit ~600 req/min/endpoint;
  no published usage fee. Instagram: **200 calls/hour per IG Business
  account**. Both are free at this project's scale. *Rate-limit figures are
  blog-sourced; confirm against the official rate-limit docs in Phase 2.*
- **[PRIMARY]** **Watch-time, retention curves and rewatch data are only
  ever exposed to the account owner**, on both platforms. For third-party
  videos this data does not exist at any price. Hard structural ceiling on
  what §3 of the original brief ("algorithm research laboratory") can ever
  say about other creators' videos.
- **[SECONDARY]** TikTok's **Content Posting API** (future, §7 Phase 11)
  requires an app audit: demo video, privacy policy, ToS URL, specific
  use-case description; no formal business verification. Budget 1–3 weeks
  and one rejection round.
  ([Content Posting API](https://developers.tiktok.com/docs/en/content-posting-api-get-started))

### 2.2 Data access — niche discovery (official path largely closed)

- **[PRIMARY]** TikTok's **Research API** is closed to this project.
  TikTok's own eligibility page requires the applicant to be affiliated
  with an academic institution (US, EEA, UK, Canada, Switzerland) or an
  EU not-for-profit/independent research body, **be "independent of
  commercial interests" and able to conduct the research "on a
  not-for-profit or non-commercial basis in pursuit of a public-interest
  mission,"** disclose funding, submit a research proposal, and **provide
  evidence of ethical review**. Unavailable to ViralLab now and under any
  future business model — the non-commercial requirement is explicit, not
  inferred.
  ([TikTok Research Tools: Access and Eligibility](https://developers.tiktok.com/products/research-api/))
- **[SECONDARY]** Instagram offers **Hashtag Search** and **Business
  Discovery** on the Facebook Login path — genuinely useful but narrow:
  Business Discovery returns public metrics for a *named* Business/Creator
  account, and hashtag search is capped by the same 200 req/hr budget.
  Neither provides broad niche crawling.
- **Conclusion unchanged:** systematic niche discovery requires
  **third-party commercial data providers**. Phase 0A now tests this before
  anything gets built on the assumption that it works.

### 2.3 Legal posture of third-party collection

- **[SECONDARY — case summary, not the docket]** *hiQ Labs v. LinkedIn* (9th Cir.): scraping publicly
  accessible, logged-out data is generally **not** a CFAA violation — but a
  later district-court ruling found hiQ **did** breach LinkedIn's ToS,
  ending in a permanent injunction and a **$500,000** payment. CFAA safety
  ≠ contractual safety.
  ([summary](https://en.wikipedia.org/wiki/HiQ_Labs_v._LinkedIn))
- **[SECONDARY — case summary, not the docket]** *Meta Platforms v. Bright Data* (N.D. Cal., Jan 2024):
  Meta's Terms of Use did **not** bind a scraper accessing public pages
  **while logged out**; Meta dropped the suit a month later. A favorable but
  single district-court ruling, narrow to logged-out public data with no
  circumvention of technical controls.
  ([summary](https://www.zyte.com/blog/california-court-meta-ruling/))

**Operating policy (binding on all implementation):**
1. Public, logged-out data only. No credentialed access, no captcha/auth-wall
   circumvention, no fake accounts.
2. No storage or redistribution of personal data beyond research need; never
   build a resellable creator database.
3. Internal research input only while the project is private. Legal counsel
   **before** any commercialization that redistributes or resells collected
   third-party data — a materially different risk profile from using it to
   advise your own content.
4. Prefer established vendors over bespoke scrapers for **lower maintenance
   and better reliability** — **not** for risk transfer. Apify's terms
   explicitly push liability onto the customer ("you shall be responsible
   for compensating any damages"), so legal exposure is the same as scraping
   directly. Corrected in Rev 3 after reading the terms:
   `docs/benchmarks/0A-tos-review.md` §1.
5. Aggressive rate limiting; respect `robots.txt`.
6. Re-review this section whenever platform ToS or case law shifts. This is
   the fastest-moving risk in the project.

**Both case summaries above are secondary sources.** They are adequate for
setting an operating policy but **not** adequate as legal advice. Neither
should be cited to a third party, and §2.3's policy exists to keep the
project well inside the safe region regardless of how the case law is
ultimately read.

### 2.4 Third-party provider costs

- **[SECONDARY]** Apify-style TikTok actors price in the **$0.30–$3.70 per
  1,000 results** band depending on actor and plan tier; the lowest
  per-unit rates require an expensive monthly plan (~$999/mo) that is not
  justified at this project's volume.
  ([example actor pricing](https://apify.com/marsultor/tiktok-post-scraper))
- **[PRIMARY — enables a zero-cost probe]** Apify's own pricing page lists a
  **Free plan: $0/month, no credit card, with $5 of platform/Store credit**
  (credits expire monthly and cannot be topped up on the free plan).
  At the per-result rates above, $5 buys roughly **1,300–1,600 results** —
  enough to run Phase 0A's ~500-video probe **without any payment**. This
  is the preferred route; see the permission request in
  `docs/benchmarks/0A-data-access.md`.
  ([Apify pricing](https://apify.com/pricing))
- **[OPEN]** Whether any provider's coverage is *complete and reliable
  enough*, and whether providers return **media** or metadata only — this is
  the central question of Phase 0A, not a planning-stage answer.

### 2.5 Local AI hardware fit (Windows RTX 2070, 8 GB VRAM)

- **[SECONDARY]** **Qwen2.5-VL-7B-Instruct**: ~18 GB VRAM at FP16 (does not
  fit), ~9 GB at INT8 (does not reliably fit with KV-cache overhead),
  **~4.5 GB at INT4** (fits with headroom). → **INT4 (AWQ or GGUF) is the
  working assumption for the local VLM**, to be confirmed by direct
  measurement in Phase 0B. All three figures are third-party estimates.
  ([VRAM requirements](https://gigagpu.com/qwen-vram-requirements/))
- **[SECONDARY]** Full fine-tuning of a 7B VLM needs ~4× inference VRAM —
  **not feasible** on this card. Any fine-tuning would require rented cloud
  GPU, subject to the spending-cap rules.
- **[ASSUMPTION — Phase 0B measures this]** `faster-whisper` INT8 should
  exceed real-time on an RTX 2070. Published figures run ~11× (RTX 3090
  FP16) to ~12× (RTX 4070 INT8); the 2070 is Turing-generation and will land
  below these. **No published RTX 2070 benchmark was found.** Nothing in
  §10 may be treated as fact until Phase 0B measures it.
  ([GPU RTF table](https://gigagpu.com/whisper-large-v3-rtf-by-gpu/))
- **[MEASURED 0B]** The machine is an **RTX 2070 SUPER** (not a plain 2070),
  8192 MiB, with **~1517 MiB already held by the desktop at idle**.
  Qwen2.5-VL 7B Q4 peaks at **7634 MiB — 93% of the card, ~558 MiB spare**,
  so the `[SECONDARY]` ~4.5 GiB INT4 estimate was too optimistic. Phase 1
  must check free VRAM before leasing a vision batch.
- **[MEASURED 0B]** Python 3.14 (the system default) has no usable ML wheels;
  a **Python 3.12 venv is required**.
- **[DESIGN CONSTRAINT — confirmed by measurement]** Qwen-VL and faster-whisper cannot be co-resident
  on 8 GB. The GPU worker processes **one modality at a time per job** and
  frees VRAM between stages. This is a hard pipeline rule.

### 2.6 What this rules out

- No reconstruction of platform ranking algorithms — correlational analysis
  only, labeled as such.
- No competitor watch-time/retention data, ever.
- No TikTok Research API, under any business model.
- No official-API media download for your own TikToks → local masters
  (§2.1).
- No exhaustive niche crawling — discovery is sampled and scheduled
  (§5.2).

---

## 3. System Architecture

Unchanged in shape from Revision 1; the additions are the media-ingest
watcher, the tier column on analysis jobs, and the hardened job protocol.
**Total long-running processes: three** (Postgres, the Ubuntu FastAPI app,
the Windows worker). No new services were added in Revision 2.

```
                         ┌──────────────────────────────────────────────┐
                         │   UBUNTU SERVER (always-on, no GPU)           │
                         │                                                │
  TikTok Display API ───▶│  ┌────────────┐   ┌───────────────────────┐  │
  (own: metadata only)   │  │ Collectors │──▶│ PostgreSQL + pgvector │  │
  IG Graph API (FB Login)│  └────────────┘   │                        │  │
  (own: metadata+media) ▶│  ┌────────────┐   │ creators, videos,      │  │
                         │  │ Discovery  │──▶│ metrics_snapshots,     │  │
  3rd-party provider ───▶│  └────────────┘   │ video_features(tier),  │  │
                         │  ┌────────────┐   │ embeddings, labels,    │  │
  Watched folder ───────▶│  │Media ingest│──▶│ samples, models,       │  │
  (your own masters)     │  └────────────┘   │ recommendations,       │  │
                         │  ┌────────────┐   │ experiments, outcomes, │  │
                         │  │ Scheduler  │──▶│ jobs                   │  │
                         │  │(APScheduler)   └──────────┬────────────┘  │
                         │  └────────────┘              │                │
                         │  ┌───────────────────────────▼─────────────┐ │
                         │  │ Job queue: Postgres SELECT…SKIP LOCKED  │ │
                         │  │ + lease tokens, heartbeats, reaper      │ │
                         │  └───────────────────────────┬─────────────┘ │
                         │  ┌───────────────────────────▼─────────────┐ │
                         │  │ FastAPI (TLS, per-worker token):        │ │
                         │  │ /jobs/*, signed media URLs, dashboard   │ │
                         │  └─────────────────────────────────────────┘ │
                         └──────────────────────────────────────────────┘
                                       ▲  │
                     poll + heartbeat  │  │ signed short-lived media URL
                     (TLS + token)     │  │ + idempotent result submit
                                       │  ▼
                         ┌──────────────────────────────────────────────┐
                         │  WINDOWS PC (RTX 2070 8 GB, not always on)    │
                         │  gpu_worker — one modality in VRAM at a time: │
                         │   faster-whisper → Qwen2.5-VL INT4 →          │
                         │   OpenCV/PySceneDetect → librosa/Essentia     │
                         └──────────────────────────────────────────────┘
```

**Pull model, not push.** The worker always initiates (`GET /jobs/next`).
If Windows is off, nothing calls out to it and nothing times out waiting;
GPU jobs accumulate as rows until it returns. Queue depth is surfaced on the
dashboard so a backlog is visible rather than silent.

---

## 4. Modules, Technology Choices & Rationale

### 4.1 Technology table

| Module | Technology | Rationale |
|---|---|---|
| Relational store | **PostgreSQL 16+** | Required by spec; JSONB for flexible feature payloads alongside typed columns for hot query paths. |
| Vector search | **pgvector** in the same instance | Tens of thousands of vectors, not billions. A separate Qdrant/Milvus would be a second stateful service for no gain — rejected. |
| Job queue | **Postgres + `SELECT … FOR UPDATE SKIP LOCKED`** | Considered Celery+Redis and rejected: a second always-on stateful service on an 8–16 GB box, with its own failure modes, for a single-user, low-throughput workload. The SQL queue gives atomic dequeue, durability and resumability on infrastructure already running. Revisit only if one Postgres can no longer arbitrate the load (not expected). |
| Scheduler | **APScheduler** in-process | Cron-like scheduling without a separate service. |
| API / orchestration | **FastAPI** | Python-first; async suits I/O-bound collectors; Pydantic models double as the job/feature contracts shared with the worker. |
| Dashboard | **FastAPI + Jinja2 + HTMX** | Spec de-prioritizes UI. Avoids a Node/SPA build pipeline for a single user. React is a sane *future* upgrade if commercialization happens. |
| Transcription | **faster-whisper (CTranslate2, INT8)** | ~4× throughput and ~40% less VRAM than reference Whisper at comparable quality; fully local. |
| Vision-language | **Qwen2.5-VL-7B-Instruct, INT4** | Only configuration fitting 8 GB (§2.5). |
| OCR cross-check | **PaddleOCR or EasyOCR** (benchmark in 0B) | Cheap second pass for small/fast-cut on-screen text the VLM misses. |
| Classic CV | **OpenCV + PySceneDetect** | Shot boundaries, optical-flow camera motion, face/framing metrics — deterministic and cheap; complements rather than duplicates the VLM. |
| Audio / music | **librosa + Essentia** (both benchmarked in 0B) | Essentia's music descriptors matter for piano repertoire; librosa for general-purpose features and ecosystem. |
| Modeling | **LightGBM** primary, scikit-learn for baselines/metrics/calibration | CPU-only training on Ubuntu; fast on few-thousand-row, hundreds-of-feature data; native missing-value and categorical handling. XGBoost equivalent in accuracy at this scale; LightGBM chosen for CPU training speed. |
| Secrets | **gitignored `.env`** + restrictive file permissions | Single operator. A secrets manager is unjustified now; CLAUDE.md flags revisiting before multi-user/commercial use. |
| Cross-machine transport | **HTTPS + per-worker bearer token + short-lived signed media URLs** | See §4.2. Simpler to secure across a Windows/Linux boundary than SMB, and reuses one auth mechanism. |

All collectors and the discovery module implement one `DataSourceAdapter`
interface (fetch-by-account, fetch-by-niche, normalize-to-schema), so
replacing a provider — expected, given §2.3's volatility — never touches the
schema or downstream modules.

### 4.2 Job-processing guarantees (new in Revision 2)

Implemented entirely as columns and routes on existing components — no new
service, no new dependency.

- **Idempotency.** Every job carries
  `idempotency_key = {job_type}:{video_id}:{method_version}` under a unique
  index. Enqueueing a duplicate is a no-op, not a second row. Results are
  written by **upsert keyed on that key**, so a replayed or retried job
  overwrites its own prior result rather than inserting a duplicate feature
  row.
- **Lease ownership validation.** Dequeue mints a random `lease_token`
  (UUID) stored on the row and returned to the worker. Every subsequent
  heartbeat, result submission and completion call **must present the
  matching token**. A worker whose lease expired and was reaped therefore
  **cannot** write results later — this closes the zombie-worker race where
  a stale result overwrites a fresher one. Token mismatch returns 409 and is
  logged.
- **Heartbeats.** Long GPU jobs extend `lease_expires_at` periodically. The
  reaper requeues only leases that **stopped heartbeating**, so a slow-but-
  alive job is never killed for being slow.
- **Bounded retries.** `attempt_count` against `max_attempts` (default 3),
  exponential backoff via a `not_before` timestamp, then terminal `failed`
  with the error text recorded. A poison job can never loop forever;
  dead-lettered jobs appear on the dashboard.
- **Transport security.** API binds to the LAN interface only, firewalled,
  **never exposed to the internet**. TLS with a self-signed CA pinned on the
  worker, plus a per-worker bearer token (rotatable, stored in `.env`).
  Media is fetched through **HMAC-signed, short-lived, single-video URLs**,
  not a blanket static-file mount.
- **Clean-up.** The worker deletes its local copy of a media file only after
  the server confirms the result was persisted.

---

## 5. Database & Processing Pipeline

### 5.1 Core tables

- `creators` — platform, handle, follower-count history, niche tags, `is_own_account`
- `videos` — platform, creator, external id, caption, hashtags, posted_at, duration, `source` (`official_api|provider|local_master`), `media_status` (`absent|available|purged`), `local_master_path`
- `metrics_snapshots` — append-only (video, captured_at, views, likes, comments, shares, saves…) so trajectory is queryable, not just the latest value
- `video_features` — video, **`tier`** (1/2/3), JSONB payload + typed columns for hot fields (hook_type, shot_count, avg_shot_length_s, speech_present, music_bpm, on_screen_text…), `method_version`
- `video_embeddings` — pgvector column, `model_version`
- `creator_baselines` — rolling robust statistics (median, MAD, window)
- `performance_labels` — `outlier_high|average|outlier_low`, traceable to the baseline row and `method_version` that produced it
- **`analysis_samples`** *(new)* — which videos were selected for Tier 2/3, the stratum, and **the sampling probability** (needed for inverse-probability weighting; see §5.2)
- `jobs` — type, payload, status, `capability_required`, `tier`, `idempotency_key`, `lease_token`, `lease_expires_at`, `attempt_count`, `max_attempts`, `not_before`, result
- `models` — name, version, trained_at, train window, validation metrics JSONB, artifact path
- `recommendations` — content JSONB, evidence list of `{claim, supporting_stat, data_source, confidence, evidence_class}`, status
- **`experiments`** *(new)* — pre-registered experiment definitions, arm assignments, pre-registered predictions (§7 Phase 8)
- `outcomes` — recommendation → published video → measured vs predicted

### 5.2 Progressive analysis (new in Revision 2)

Cost is driven by the **tier mix**, not by total video count. Each tier is a
job type, distinguished by the `tier` column — not a separate service.

| Tier | What runs | Where | Applied to |
|---|---|---|---|
| **1 — metadata screening** | duration, caption/hashtag text features + text embedding, posting time/day, creator stats, metric trajectory, cover-image-only cheap features | Ubuntu CPU, ~free | **100%** of ingested videos |
| **2 — selective multimodal** | faster-whisper transcript, sampled-frame Qwen-VL captioning, shot detection, core audio features | Windows GPU | **stratified sample** (below) |
| **3 — deep analysis** | dense frame sampling, per-shot VLM captioning, OCR pass, detailed music/structure analysis | Windows GPU, expensive | small justified subset: **all of your own videos** + the clearest niche outliers |

**Stratified sampling is mandatory, and this is a statistical requirement,
not a budget convenience.** Tier 2/3 selection must be stratified across
`outlier_high` / `average` / `outlier_low`, and across creators and time
windows. Analysing only the winners is the single most likely way this
project produces confident nonsense: without average and underperforming
videos, no model can learn what *distinguishes* a hit, because the features
common to all videos in the niche would look like success factors.

Every selection writes its **sampling probability** to `analysis_samples`,
so modeling can apply inverse-probability weighting and recover unbiased
base rates from a deliberately non-uniform sample. A stratified sample
analysed as if it were random is a silent bias; recording the weights makes
the correction possible.

### 5.3 Flow

Collect/discover → `videos` + `metrics_snapshots` → **Tier 1 screening
(all)** → baseline + outlier labeling → **stratified selection** → Tier 2
GPU features → optional Tier 3 → embeddings → modeling (weighted,
chronologically split) → `models` → recommendation generation → **live
experiment** → `outcomes` → retraining.

### 5.4 Fixed-age metric comparison (new in Revision 3)

**No two videos may be compared on metrics captured at different ages.**
A video read at 3 days old and one read at 300 days old have had wildly
unequal exposure; comparing their view counts measures elapsed time, not
content quality. Every metric used for labeling, modeling, or experiment
analysis must be an **age-anchored** value: **views@7d** and **views@30d**
are the two canonical anchors (plus **views@90d** where available for
long-tail behaviour).

This requirement has sharply different consequences for the two data
sources, and it is the reason §7 Phase 0A now gates on timestamp quality.

**Own accounts — exact, free, and easy.** Daily scheduled snapshots via the
official APIs give a true trajectory per video, so views@7d and views@30d
are read directly off `metrics_snapshots`. Two adjustments: read the 7-day
anchor at **day 9+** because Instagram's data lags up to 48 hours (§2.1),
and interpolate only across gaps of ≤2 days, flagging anything wider rather
than silently filling it.

**Third-party niche videos — the hard case.** A provider scrape returns
metrics at whatever age each video happens to be, which is unknown unless
the provider supplies a reliable `posted_at`/`create_time`. Two viable
strategies, with different costs:

| Strategy | How | Cost | Weakness |
|---|---|---|---|
| **A — Age-gated mature snapshot** *(default)* | Only admit videos **older than the anchor** (e.g. ≥35 days for a 30-day anchor), treating the single reading as the mature value | 1 billed result per video | Not a true 7-day value; assumes accumulation has substantially flattened by the anchor age — **an assumption that must itself be tested**, not asserted |
| **B — Longitudinal re-query** | Discover videos young, re-query each at fixed intervals to interpolate the anchors | **N× cost** (each re-query is a billed result) | Far more expensive; requires catching videos early |

**Design decision:** use **Strategy A for the bulk training corpus** (cheap
and valid, provided `posted_at` is trustworthy), and **Strategy B only for
a small trend-monitoring cohort** where early trajectory is the actual
object of study. Own-account data always uses true snapshots.

**The flattening assumption is itself a research question.** Strategy A
rests on "views have substantially stopped accumulating by age *X*," which
is **[OPEN]** — TikTok in particular is known to resurface older content.
It is testable for free on your own account the moment Phase 2 has a few
weeks of daily snapshots: plot the share of 90-day views accumulated by day
7 and day 30. Until that curve exists, Strategy A's anchor age is a
provisional choice, and §11 tracks it as an open question.

**Schema consequence:** `metrics_snapshots` already stores `captured_at`;
the plan adds a derived, explicitly-labeled `metrics_at_age` view rather
than overwriting raw readings. Raw snapshots are never mutated — age
anchoring is a read-time derivation, so a change of anchor policy never
destroys collected data.

---

## 6. Two-Computer Deployment

- **Ubuntu** runs continuously: Postgres, the FastAPI app (API + scheduler +
  dashboard; one process to start, splittable later), collectors, the media-
  ingest watcher, Tier 1 screening, baseline/label computation, LightGBM
  training, and the lease reaper. 8–16 GB RAM is ample; no raw video decoding
  happens here.
- **Windows** runs `gpu_worker` as a long-lived process (Task Scheduler at
  logon, or started manually before a batch). It polls for
  `capability=gpu` jobs, fetches media via signed URL, runs modality
  pipelines sequentially, heartbeats its lease, submits results
  idempotently, and deletes its local copy after confirmation. When off,
  nothing on Ubuntu blocks.

---

## 7. Phased Implementation Plan

> **`docs/PRODUCT_VISION.md` is the definitive product specification.**
> Where this plan and that document disagree about *what* is being built,
> the vision document is correct and this plan is corrected. Where they
> disagree about *how or when*, this plan is correct.
>
> **Roadmap sequence (Revision 4, 2026-10-24).** The phases below were
> renumbered to put the product before further historical analysis:
>
> | # | Phase | State |
> |---|---|---|
> | 0A/0B | Feasibility probes | ✅ complete |
> | 1 | Infrastructure, queue, API | ✅ complete |
> | 2 | Own-account collection, media, matching | ✅ substantially complete; 7-day acceptance pending |
> | 3 | Unified GUI + Phase 4 foundations | ✅ complete |
> | 4 | Competitor discovery + targeted creator research | ✅ complete |
> | 4.5 | Live competitive intelligence: discovery, timeline, report, collections | ✅ delivered 2026-10-26 |
> | **6A** | **Hook intelligence on real own-account video** | **✅ delivered 2026-10-26** |
> | 5 | Breakthrough analysis + baselines | planned |
> | 6 | Multimodal feature extraction | planned |
> | 7 | Comparative analytics + temporal intelligence | planned |
> | 8 | Evidence-based AI creative strategist | planned |
> | 9 | Live experiment + closed-loop learning | planned (MVP ends here) |
> | 10 | Automated retraining cadence | post-MVP |
> | 11 | Cross-niche trend intelligence | post-MVP |
> | 12 | Production, scheduling, publishing | post-MVP |
>
> The old Phase 3 (niche discovery) is now Phase 4; old Phases 4-8 shift by
> one and are consolidated as described below. **No requirement or
> architecture decision from the previous numbering has been dropped** —
> §12's validation protocol, §5.4's fixed-age rules and every ADR still
> apply, and the acceptance criteria below incorporate the originals.

**MVP = Phases 0A through 8.** The live experiment is now inside the MVP:
the MVP must test whether recommendations *improve outcomes*, not merely
whether recommendations are produced.

### Phase 0A — Data-Access Feasibility Probe *(new; hard gate)*
Throwaway scripts only — **no infrastructure, no schema, no services.**
Determine, empirically:
- Which third-party providers actually return usable TikTok/Reels data for
  your niche, with what field completeness (do we get saves? posting time?
  reliable follower counts?).
- **Whether media files are obtainable at all**, per provider, and under
  what terms — distinguishing metadata-only from media-bearing responses
  (§2.1's asymmetry makes this decisive for Tier 2/3 feasibility).
- **Timestamp quality (new in Rev 3, now a blocking criterion).** Does the
  provider return a reliable absolute `posted_at`/`create_time`? Without it,
  video age is unknown, fixed-age comparison (§5.4) is impossible, and the
  data is unusable for labeling or modeling **at any price**. Verify by
  cross-checking a sample of returned timestamps against the public post
  dates on the videos themselves — a plausible-looking field that is
  actually a scrape time, a relative age, or a rounded date fails this
  check.
- **Re-query stability.** Can the same video be fetched again later by a
  stable id, so Strategy B (§5.4) is available for the trend cohort? Test
  by fetching the same ~20 videos twice, some days apart, and confirming id
  stability and metric movement.
- Real cost per 1,000 results, measured on actual usage, not quoted from a
  pricing page.
- Actual availability/retention: how far back does a provider's data go, how
  often are records missing or stale.
- Legal review of the chosen provider's ToS against the §2.3 policy.

**Gate — do not proceed to Phase 1 unless all four hold:**
1. At least one provider returns **≥80% completeness on BLOCKING + IMPORTANT
   fields** (scorecard in `probes/data_access/field_requirements.py`), with
   **100% on every BLOCKING field** — `posted_at` among them.
2. Timestamp quality passes the cross-check above.
3. Measured cost is consistent with §10's projection band.
4. Provider terms are compatible with §2.3, reviewed in writing.

**And** the media-availability question is answered either way in writing.
A negative result here reshapes or stops the project, which is exactly why
it runs before any infrastructure is built.
**Deliverable:** `docs/benchmarks/0A-data-access.md`.

### Phase 0B — Local AI Benchmark Probe *(new; hard gate, parallel with 0A)*
Hand-collect **20–50 representative videos** (your own masters + a spread of
niche videos spanning short/long, speech-heavy/music-only, static/high-
motion). Run on the actual RTX 2070:
- faster-whisper at several model sizes and compute types → real-time factor,
  peak VRAM, transcript quality spot-check (including on piano-performance
  audio, where music may degrade ASR).
- Qwen2.5-VL INT4 (AWQ vs GGUF) → seconds/video at a fixed frame-sampling
  rate, peak VRAM, and a **qualitative check of whether its output is
  actually useful** for hook/framing/tone description — a fast model that
  produces vague captions is worthless.
- OpenCV/PySceneDetect, librosa vs Essentia → seconds/video.
- Full Tier 2 chain end-to-end → **measured seconds/video**, plus a Tier 3
  measurement on a handful.
- Confirm the one-modality-at-a-time VRAM discipline holds in practice.

**Total end-to-end cost accounting (new in Rev 3).** Per-video inference
time alone understates real cost. 0B must measure and report every
component separately:

| Component | Why it matters |
|---|---|
| **Model cold-load time**, per model, measured separately from inference | Potentially the dominant cost — see below |
| Media **download** time over the LAN (and from provider, if applicable) | Scales with file size, not video length |
| **Decode/frame-extraction** time (ffmpeg) | Often non-trivial vs. inference for short clips |
| Per-video **inference** time, per modality | The only component Rev 2 measured |
| **Tier 3** end-to-end, separately from Tier 2 | Dense sampling + OCR may be many× Tier 2, not slightly more |
| **Storage footprint actually observed** — media, extracted frames, transcripts, embeddings | Replaces the unverified ~10 MB/video assumption |
| **Peak VRAM and system RAM** per stage | Confirms the 8 GB ceiling holds with real inputs |
| **Idle/retry overhead** — failed jobs, OOM retries | Real throughput is below best-case throughput |

**Why cold-load time is called out specifically:** if loading INT4
Qwen2.5-VL takes on the order of 30–60 s while analysing a 20-second clip
takes a few seconds, then **model loading dominates total cost** under a
naive one-job-per-video design, and measured per-video inference time would
badly misrepresent real throughput. The fix is to **batch by modality
rather than by video** — lease a batch, run transcription across all of it,
unload, load the VLM, run vision across all of it — which amortises each
load over many videos while still honouring the one-modality-at-a-time VRAM
rule.

That change affects job granularity, so **it is not being decided now.**
0B measures load vs. inference time; if the ratio confirms the concern, the
batching design and its interaction with per-video idempotency (§4.2) is
settled in an ADR **before** Phase 1 builds the job schema — which is
precisely the kind of decision that would have been expensive to discover
after the infrastructure existed.

**Gate — do not proceed to Phase 1 unless:** measured **total** Tier 2 cost
(all components above, not inference alone) makes the §10 cost model viable
at the intended volume, **and** VLM output quality passes your own
qualitative review. If the VLM is too vague or too slow,
decide here whether to drop to a smaller/alternative model, reduce frame
sampling, or narrow Tier 2 scope — *before* an entire pipeline is built
around it.
**Deliverable:** `docs/benchmarks/0B-local-ai.md`, which **replaces every
`[ASSUMPTION]` in §2.5 and §10 with measurements**, and an ADR recording the
chosen configuration.

### Phase 1 — Infrastructure Scaffold
Repo structure (§9), Postgres schema + Alembic migrations, the hardened
`jobs` table and queue functions (idempotency key, lease token, heartbeat,
bounded retries, reaper), FastAPI skeleton with TLS + per-worker token and
signed media URLs, `gpu_worker` skeleton, shared Pydantic contracts,
`.env` config including the spending cap.
**Acceptance:** a no-op job round-trips Ubuntu→Windows over the real LAN
over TLS; **and** these four adversarial tests pass — (1) worker killed
mid-job is requeued by the reaper after lease expiry; (2) a *resurrected*
worker presenting a stale `lease_token` is rejected with 409 and cannot
overwrite the newer result; (3) the same job replayed twice produces exactly
one result row; (4) a deliberately failing job stops after `max_attempts`
and is dead-lettered, not retried forever.

### Phase 2 — Own-Account Collection
TikTok Display API + Instagram Graph API (**Facebook Login path**)
collectors: OAuth, token refresh. **[PRIMARY, ADR 0005]** TikTok's access
token lasts 24 h and its **refresh token rotates on every refresh** — both
must be persisted together or auth dies one cycle later. On the Facebook
Login path the Instagram credential is a **Page token, which does not
expire**; the 60-day lifetime belongs to the *User* token it is derived
from, and `ig_refresh_token` is the Instagram-Login path we do not use.
IG `media_url` before expiry. **Media-ingest watcher** for your own TikTok
masters, with fingerprint linking and manual-link fallback in the dashboard.
Empirically confirm the actual returned field set against the docs (e.g.
whether TikTok returns any saves field) and record the verification in an
ADR.
**Acceptance:** complete historical video list + metrics for your own TikTok and
Instagram accounts stored; your own masters linked to their metadata rows;
scheduled collection runs unattended for **7 consecutive days** with no
manual re-auth, no crash, and no missed IG media download.

**Progress (2026-10-24).** TikTok: ✅ authorized, 234 videos + 234 snapshots,
0 validity problems, completeness confirmed against API pagination
(`docs/benchmarks/2-tiktok-live-validation.md`). Daily schedule implemented
with bounded retries, 429 backoff, run logging and a restart-safe due check.
**Migrated to the authoritative server (2026-10-24).** Ubuntu now holds the
data and runs the only collector; Windows is GPU-worker-only and refuses to
collect. Original video ids and snapshot `captured_at` values were preserved
verbatim — observations were copied, not re-collected, so the trajectory that
fixed-age anchoring depends on survives. All counts and metric sums verified
identical, 0 orphans.

**Progress (2026-10-24, matching and cross-platform integration).**
Instagram: ✅ authorized (Facebook Login path), 289 publications, 289
snapshots, **258 media files preserved (736 MB)** at 90.2% of what is
preservable. Media preservation is now a **deployed, scheduled module**
(`server/collectors/media_preservation.py`) that runs at the end of every
Instagram collection pass, isolated so a media failure can never cost a
metrics snapshot. Previously it was a hand-run script, so any Reel collected
on a day nobody ran it lost its media permanently once the CDN link expired.

**Content relationship model.** Three layers now distinct (migration
`c7a1d4e9b082`): **performance** (one reading of one work) → **variant**
(`local_masters`, one export with its own hook/overlays/trim) →
**publication** (`videos`, one platform at one time with independent
metrics). 156 performances across 250 masters, 63 with more than one variant.
Plus `publication_links` for cross-platform pairs, which deliberately has no
`auto` status. Existing tables were extended, not replaced.

**Audio fingerprinting replaced, after measurement.** The 32-bin loudness
envelope was measured against 24 known positives and 10 known negatives and
**failed**: on a different opening hook it scored genuine matches 0.563–0.781,
*below* what it scored on true negatives (0.799) — gap **−0.236**. Replaced
by a spectral band-difference fingerprint (numpy only, no ML): same
transforms score 0.862–1.000 against negatives ≤0.425 — gap **+0.437**.
`docs/benchmarks/2-audio-fingerprint.md`. Audio is now the **gate** and
visuals the corroboration, because ~99% cross-posting means the normal case
is *same recording, visibly different edit*.

**Matching validated.** The 93 earlier auto-links were re-scored: **92
identical, 1 corrected to a better variant of the same performance, 0
contradicted**, plus 36 pairs the envelope had missed. Now 129 auto-linked
(50% of preserved Instagram publications), 27 pending review, 102 unmatched
for archival reasons. `[ESTIMATE]` 100% method agreement is **not** measured
precision — both methods share the same frame hashing — so a stratified
12-link human sample is queued for ground truth.

**Cross-platform.** TikTok cover images were implemented and hashed
(234/234, 0 failures) and then **measured and rejected**: median
cover-vs-master Hamming 16/64, distinctive on 1 of 234, and 2 fps re-sampling
did not help. So TikTok↔Instagram rests on duration + date + caption, scored
per-signal with mutual-best pairing: **201 proposals (136 strong)**, none
confirmed. `docs/benchmarks/2-cross-platform-matching.md`.

**Review UI.** `scripts/review_ui.py` — side-by-side playback of local master
and published clip with range-request seeking, the full evidence behind each
proposal, and four answers including *"same performance, different edit"*.
Decisions are written immediately and attributed to `human`; the queue
resumes where it was and **never blocks other work**.

**Defects found and fixed this phase** (each now has a regression test):
a sub-frame alignment bug that made a correct 1 s offset score 0.448 instead
of 0.999; a coverage bug letting two unrelated files score **1.000 on 22
aligned frames** and chain 33 unrelated performances into one group; **three
image posts stored as `.mp4` and counted as preserved video** because ffprobe
succeeds on a JPEG; a `repr`+`literal_eval` database transport that silently
truncated every caption over ~250 characters; and `reels_skip_rate` being
discarded because the insight parser kept only `int` values.

**Outstanding for Phase 2 closure:** the 7-day unattended run has **NOT**
elapsed and will not be claimed before it has (started 2026-10-24; earliest
honest claim 2026-10-31). Owner decisions pending: 27 master links, 201
cross-platform proposals, 156 inferred performance groupings — none blocking.
The TikTok client secret is **not** rotated, per the owner's explicit
decision. Windows source records and backups are retained pending approval
for cleanup.

**Phase 3 readiness.** `docs/phase3-data-readiness.md` records what the data
can and cannot support. The blocking fact: **every snapshot was captured on a
single day**, at ages from 1 to 1357 days, so **no genuine `views@7d` or
`views@30d` exists for any current publication** and none may be
reconstructed. Instagram retention is now collected
(`ig_reels_avg_watch_time`, `ig_reels_video_view_total_time`,
`reels_skip_rate` — all verified live) in a separate call so an
"in development" metric cannot take down a pass; TikTok exposes **no**
retention and **no** saves, which stay NULL and must never be coalesced to
zero (verified: 702 TikTok snapshots, `saves` NULL in all 702, value 0 in
none).

### Phase 3 — Unified GUI and Phase 4 Foundations  **(in progress)**

The first genuinely usable interface, plus the data and safety foundations
competitor discovery needs. Rationale: every capability so far has been a
separate command-line script, which `docs/PRODUCT_VISION.md` §3.I
explicitly forbids continuing.

**Milestone 3.1 — Typed API + no-build SPA.** ✅ *implemented*
`/api/*` with Pydantic response models (`shared/schemas/ui.py`), a static
ES-module SPA in `webui/`, and `webui/types.js` generated from OpenAPI so
the contract is checked rather than transcribed. Stack rationale in
**ADR 0006** — React/Vite was rejected on measured grounds (184 MiB free
RAM on the server; deployment is a file copy).
*Acceptance:* a test fails if the generated types drift from the models.
✅ met.

**Milestone 3.2 — Dashboard, library, detail, review.** ✅ *implemented*
Connected platforms and health; searchable/filterable publication list;
detail with snapshot history, provenance, master links and cross-platform
relationships; review of master links and cross-platform pairs with four
answers including *"same performance, different edit"*.
*Acceptance:* every performance figure renders with its observation age; an
unreported metric renders as unknown, never 0; a machine proposal is
visually distinct from a human decision; no filesystem path reaches the
browser. ✅ met, 32 API tests.

**Milestone 3.3 — Access control.** ✅ *implemented*
Signed HttpOnly SameSite=Strict session cookie; mutations additionally
require a header a cross-site form cannot set; `/status/*` no longer open;
media only via short-lived signed single-video URLs.
*Acceptance:* unauthenticated reads refused, mutation without the header
refused. ✅ met.

**Milestone 3.4 — Spending guard.** ✅ *implemented*
`server/config.py` makes `spending.monthly_cap_usd` real. It shipped as
`0.00` with no authorised providers and was **read by nothing**; discovery
needs a paid provider, so this is now the single door every paid call goes
through, refusing by default.
*Acceptance:* a paid call is refused with the shipped configuration; the
cap cannot be exceeded; refusal is non-retryable. ✅ met, 15 tests.

**Milestone 3.5 — Phase 4 data foundations.** ✅ *implemented*
Migration `b5f2a81c04d7`: `collection_runs.cost_usd`/`provider`, creator
provenance columns, and append-only `creator_snapshots` so follower history
is never fabricated. Full forward plan in `docs/SCHEMA_PLAN.md`.
*Acceptance:* migration applies and reverses on the authoritative database;
a discovered creator is distinguishable from an own account in the same
table. ✅ met.

**Milestone 3.6 — Phase 2 reliability closure.** ◐ *partially met*
*(Updated 2026-10-25: the unattended collector has now run successfully on
2026-10-24 and 2026-10-25 for both platforms, giving a second capture day.
The window still has to elapse.)*
Media preservation audited: per-item commits, orphan adoption, `.part`
sweeping, database-backed deduplication, cap no longer consuming attempts.
A screen-veto recall bug was found and fixed (+31 genuine variant edges).
*Acceptance:* 7 consecutive unattended collection days — **not yet
elapsed**, earliest legitimate completion **2026-10-31**.

**Dependencies:** none outstanding.
**Blocked:** nothing.

---

### Phase 4 — Competitor Discovery and Targeted Creator Research  **(research delivered 2026-10-25)**

The capability that makes ViralLab more than a personal analytics tool.
`[PRIMARY, ADR 0001]` Official APIs expose no competitor data, so this is
third-party collection under §2.3, and the pipeline shape is fixed by Phase
0A measurement:

> **discover creators via hashtag/keyword → fetch each creator's
> back-catalogue by profile → build the fixed-age corpus from the
> back-catalogues.**

Hashtag feeds returned 0 of 45 records aged ≥35 days, so they discover
creators and never measure them. `server/discovery/apify.py` encodes that
choice rather than leaving it to the caller.

**Milestone 4.1 — `DiscoveryAdapter` seam.** ✅ *implemented*
`server/discovery/` with a provider-agnostic interface, an Apify
implementation, and a fixture implementation that runs the **same
normalisers**. Supports creator / keyword / hashtag queries, pagination and
incremental `since` collection, bounded retries with full jitter, 429
handling, an aborting run timeout, a hard item cap, and per-item error
surfacing.

Every mapped field was taken from a **recorded live response** from the
Phase 0A probe, not from documentation. The capability declarations are the
load-bearing part: `[PRIMARY]` the validated Instagram actors return **no
follower count and no bio**, so those stay null rather than becoming zeros,
and `clockworks` rounds counts to ~3 significant figures, which travels with
the data.
*Acceptance:* a query is reproducible from its stored parameters; re-running
updates and never duplicates; every row carries provenance and recorded
coverage limits. ✅ met, 48 tests.

**Milestone 4.2 — Targeted creator research.** ✅ *implemented*
`server/research/service.py`: resolve a handle or profile URL → find-or-
create the account → reuse fresh research rather than re-collecting →
reserve budget → collect → settle or release → store idempotently → append
observations → detect change points → record research state.
*Acceptance:* researching twice reuses and spends nothing; a forced refresh
appends an observation without duplicating a publication (verified: 62
publications stay 62 while snapshots go 62 → 124); a handle and its profile
URL resolve to one creator. ✅ met.

**Milestone 4.3 — Breakthrough analysis, first version.** ✅ *implemented*
`server/research/breakthrough.py`. A breakthrough is a **sustained change in
the creator's own performance level**, not a high-view video. CUSUM locates
the boundary; robust medians either side establish significance; a sustain
test over everything after it rejects hot streaks.

Three classifications that are different claims rather than different
wordings: `observed_outlier`, `inferred_breakthrough`, and
`verified_trajectory_change` — the last of which **a single research pass
cannot reach**, and the detector says so instead of settling for the
strongest available word. Confidence is capped at 0.75 without longitudinal
evidence.
*Acceptance:* a gradual rise is not a breakthrough; a single spike is not a
breakthrough; a **declining** series produces none (the specific failure
mode where cumulative views are read as a trajectory); the planted boundary
in the demonstration corpus is located exactly. ✅ met, 23 tests.

**Milestone 4.4 — Atomic budget enforcement.** ✅ *implemented*
`server/budget.py` replaces the advisory read-and-compare guard, which
failed four ways: time-of-check/time-of-use between concurrent callers,
nothing actually reserved, a crash after the call losing the charge, and a
retry paying twice. Reservations are taken under a per-provider-month
advisory lock, keyed by idempotency key, and an unresolved reservation keeps
holding budget — because assuming an unknown call was not billed is the
assumption that overspends.
*Acceptance:* two concurrent callers cannot both exceed the cap (tested with
two real sessions); a retry reuses its reservation; a settled charge cannot
be released. ✅ met, 23 tests.

**Milestone 4.5 — Research interface.** ✅ *implemented*
`webui/research.js` plus `/api/research/*`: creator search by handle or URL
with platform selector, spending-authorisation state, research history with
staleness and refresh cost, creator profile with caveats attached, a
publication explorer sortable by **performance against the creator's own
baseline**, and breakthrough analysis showing the classification alongside
the evidence it lacks.
*Acceptance:* fixture-backed records are labelled wherever they appear; no
control exists for a capability that does not work. ✅ met, 27 tests.

**Milestone 4.6 — Keyword and hashtag discovery UI.** ⬜ *not built*
The adapter supports both query kinds and is tested, but there is no
discovery surface and no scheduled sweep.
*Depends on:* an authorised budget — a keyword sweep is the one workflow
that cannot be meaningfully demonstrated on fixtures, because its purpose is
to find creators nobody listed.

**Milestone 4.7 — Cross-platform identity reconciliation.** ⬜ *not built*
`creator_identities` and `creator_identity_members` exist with a closed
`evidence_kind` set that deliberately omits "same handle", so a coincidental
name match is unrepresentable rather than merely discouraged. Nothing
populates them yet.
*Depends on:* bio links (already extracted by the clockworks normaliser) and
the Phase 2 fingerprint for cross-posted content.

**Milestone 4.8 — Ownership scoping.** ✅ *implemented, after a found defect*
Before Phase 4, every row in `videos` belonged to the owner, so filtering by
platform was *equivalent* to filtering by ownership. Competitor research broke
that equivalence, and four queries still relied on it.

Found by smoke-testing the deployed write path against the production
database: one fixture research pass moved the dashboard's TikTok publication
count from 234 to 296, and the own-account capture-day count — which governs
whether any fixed-age anchor is computable at all — was computed over
competitor snapshots too.

The serious one was not the arithmetic. `media_preservation` selected **every**
Instagram publication with no ownership filter, so the next unattended pass
after researching an Instagram competitor would have downloaded and
*permanently retained* their reels — the exact thing
`docs/MULTIMODAL_PIPELINE.md` forbids and that `analysis_seam.py` refuses for
transient analysis. That collector predates the policy and nothing connected
the two. Nothing was actually downloaded: the fixture creator was TikTok-only,
where no media is obtainable.

An audit of every remaining `select(Video)` found the same assumption in
three more places, one of them worse than the media problem:

| site | what would have happened |
|---|---|
| `media_preservation` (4 queries) | a researched competitor's reels **downloaded and permanently retained** |
| `snapshot_policy.select_for_refresh` | competitor publications have **no snapshots**, so they sort to priority 1 ("never observed") and **spend the owner's Instagram API quota**, crowding out the owner's own young posts — the only window in which a real `views@7d` anchor is capturable, and one that does not come back |
| `snapshot_policy.coverage_report` | competitor publications counted as gaps in the owner's corpus — inflating `never_observed`, depressing `mean_snapshots`, which is the report used to judge readiness before modelling |
| `scheduler.collect` (2 queries) | **nothing** — scoped for clarity, not for a defect. `videos` carries `UniqueConstraint(platform, external_id)`, so a platform-wide `by_ext` map could never have misattributed a snapshot. Recorded here because the first version of this entry claimed otherwise. |
| `ui.build_video_page` | third-party publications listed in the owner's own archive, which exists to offer signed media URLs and drive master-link review against local master files |

Fixed by scoping each to `Creator.is_own_account` — and in the collector, to
the account actually being collected, which is strictly correct because an
external id is only unique within an account. `videos.creator_id` is NOT
NULL, so none of these joins can silently drop a row that should have been
considered.

`_signed_media_url` now also refuses to sign for a non-own-account video and
logs it. That should be unreachable once preservation is scoped, but the cost
of being wrong is serving a competitor's media out of our own API, so the
policy is enforced at the point of use as well as at collection.

The 62 fixture rows were removed from production, which returned to exactly
619 videos and 1372 snapshots, and the dashboard to 234 TikTok publications,
2 capture days and 90.2% Instagram media coverage.
*Acceptance:* a competitor publication is never fetched, never adopted from
disk by filename, never counted in media coverage or corpus coverage, never
selected for refresh, never listed in the owner's library, never served as
media, and never moves any own-account figure in the overview. ✅ met, 8
regression tests, verified to fail without the fix.

**The general lesson, recorded because it will recur:** every query written
before Phase 4 that filtered `videos` by platform was *implicitly* filtering
by ownership, and stayed correct only while the owner was the sole creator in
the table. Phase 4 silently invalidated that, and the type system cannot
catch it. Any new query over `videos`, `metrics_snapshots` or `creators` must
state which population it means.

**Blocked:** nothing in this phase is blocked by us. Live collection is
**refused rather than attempted** until the owner sets
`spending.monthly_cap_usd` and `providers_authorised` in
`config/settings.yaml` and provides `APIFY_TOKEN`. Until then everything
runs against committed synthetic fixtures, labelled as such in the database,
the API and the interface. That is the correct state, not a defect.

### Phase 4.5 — Live Competitive Intelligence  **(delivered 2026-10-26)**

The transition from infrastructure to a usable product: Phase 4 could
research an account you already knew about; this phase finds accounts you
did not.

**Milestone 4.5.1 — Phase 4 readiness audit.** ✅ *three defects fixed*

| defect | consequence | fix |
|---|---|---|
| The Apify token was sent as `?token=...` on **every** request | `[PRIMARY]` https://docs.apify.com/api/v2 (checked 2026-10-25, API v2) warns that "URLs are often stored in browser history and server logs". The credential was in every URL this process built. | `Authorization: Bearer`, which the same docs recommend |
| **No in-flight guard on research.** The idempotency key was `research:{job.id}`, which protects a retry of one job — but every request minted a *new* job | Two requests arriving together each opened a job, each reserved budget under a distinct key, and each would have launched a **paid** provider run. Same class as the Phase 2 collector defect, in the path that costs money. | Per-query advisory lock plus an in-flight check, with a 30-minute staleness bound so a crashed process cannot lock a creator out |
| `anchor_eligibility` was not scoped to own accounts | A sixth site in the Phase 4.8 family. It reports how much of **our** corpus can support fixed-age comparison — the figure that governs what the analysis may claim — and competitor backfills both inflated the total and landed entirely in `backfill_only`. | Joined to `creators.is_own_account` |

*Verified clean:* the reservation is committed **before** the provider
request begins (so a crash mid-call leaves budget held, not lost); no token
reaches a log line, a response model or a browser payload; every competitor
row carries provenance and both `posted_at` and `captured_at`; the
seven-day acceptance window is untouched.

**Milestone 4.5.2 — Keyword, hashtag and niche discovery.** ✅ *implemented*
`server/research/discover.py`. The pipeline Phase 0A measurement forces:
search a concept → get publications → derive creators → deduplicate → rank
→ research the promising ones **selectively**.

Expansion is deliberately not automatic. A candidate list carries a
per-creator price and stops; `plan_expansion` walks the ranking under an
explicit creator or spend ceiling and reports what it skipped and why.
Niches are curated bundles (`classical_piano`, `chopin`, `piano_covers`, …)
that expand into individual queries, because running a whole niche in one
click would multiply cost by the number of terms without the owner seeing
the bill.

Ranking tiers — small-creator breakout, established breakout candidate,
repeatedly relevant, relevant — each carry the *reasons* they were assigned,
because a composite score with no explanation is indistinguishable from a
made-up one.
*Acceptance:* a query is reproducible from stored `params`; re-running
appends an observation without duplicating a publication; an identical
search inside 6 hours is **reused** rather than re-run; the sampling bias of
a feed travels with every result set. ✅ met, 41 tests.

**Three defects found by running it**, each now a regression test:

1. **A discovery sample poisoned the incremental watermark.** A keyword feed
   stores a handful of a creator's publications and leaves
   `research_status` at `none`. `research_creator` treated the newest of
   those as "collected up to here" and fetched only what came after —
   **33 publications of 62**, with the back-catalogue permanently invisible.
   Incremental collection is now gated on a creator having actually been
   researched.
2. **Job result sets resolved through `videos.discovery_job_id`**, which
   records only the *first* job to see a publication. Re-running a search,
   or a search that re-found a known creator, appeared to return nothing.
   Added `metrics_snapshots.discovery_job_id`: the observation is the record
   of "this job saw this publication at this moment".
3. **A views-per-follower ratio is unstable when the denominator is small.**
   A 640-follower account with a 9,300-view post scored level with a genuine
   78,400-view breakout on 1,900 followers. The ratio is now reported but
   gated behind an absolute view floor and smoothed against a pseudo-
   audience, and the explanation says why it was not ranked as a breakout.

**Milestone 4.5.3 — Discovery interface.** ✅ *implemented*
`webui/discover.js`. Free estimate before any commitment, platform and
limit selectors, niche tagging, authorisation and budget state, per-candidate
reasons and signals, filters, save-to-collection, bounded expansion, and
search history that reopens a stored result set for nothing.

**Milestone 4.5.4 — Creator breakthrough timeline.** ✅ *implemented*
`server/research/timeline.py` + `webui/timeline.js`, drawn as inline SVG
(ADR 0006: no charting library for a few dozen points).

The statistical requirement is enforced in the **data contract**, not the
copy. Every timeline declares a `mode`:

- `fixed_age` — every point is views at the same age, bracketed by real
  observations. Comparable. Solid line.
- `lifetime_exploratory` — the current reality: one lifetime figure per
  publication at whatever age it happened to be. **Not a trajectory.**
  Dashed line, warning header, and `age_days` on every point.
- `mixed` — anchored points are marked and never averaged with the rest.

Outlier candidates (a fact about one publication) are hollow rings; the
change point (a claim about the creator's level) is a labelled rule;
sustained runs are bands. `confidence_is_calibrated` is `False` everywhere,
and the note says the score "is not a probability".
*Acceptance:* a lifetime chart is never labelled a trajectory; the planted
change point is located exactly (index 34 of 62); clicking a point opens the
publication. ✅ met.

**Milestone 4.5.5 — Creator research report.** ✅ *implemented*
`server/research/report.py`. Ten sections, each declaring its own basis
(`observed` / `inferred` / `unavailable`) and carrying its own caveats.

The load-bearing section is the change-point analysis, which states **why it
was flagged** as an ordered chain with its numbers (CUSUM location, robust
medians either side, the sustain test, and the direction of the age
confound) and **what would strengthen it** (observe again in a week; collect
follower history; research a cohort). Repertoire detection is explicitly
`metadata_mention` — keyword matching over captions, hashtags and music
titles, which finds mentions, not works.
*Acceptance:* an inferred breakthrough explains itself and names its missing
evidence; no section presents a judgement as a measurement. ✅ met.

**Milestone 4.5.6 — Competitive research collections.** ✅ *implemented*
Migration `e7c2b94f83da`, `server/research/collections.py`,
`webui/collections.js`.

`purpose` is the point: a `research` group and a `control_cohort` are
different objects, and `docs/SCHEMA_PLAN.md` §3 makes a control cohort of
non-breakthrough creators in the same niche the prerequisite for promoting
any pattern above `hypothesis`. A group assembled for inspiration cannot
quietly become a control later.

Members are compared on **within-creator relative performance**, never on
lifetime views. Lifetime views are shown and labelled; sorting by them warns
that it measures audience size and elapsed time rather than content quality.
*Acceptance:* a collection carries no denormalised performance figure; an
own account cannot join one; deleting a group keeps every creator and
observation. ✅ met.

**Milestone 4.5.7 — Multimodal data contract.** ✅ *declared, nothing runs*
`FEATURE_CONTRACT` in `server/research/analysis_seam.py` declares all nine
things the next phase must identify, across `audio`, `vision` and
`cross_platform`. `request_analysis()` queues a real batch through the
existing lease/heartbeat/retry queue and reports `will_run: False` with the
reason — a request that looked accepted and silently never ran would be
worse than a refusal. Fixture publications are refused outright: features
from fabricated media would be fabricated features.

Every produced item must carry `confidence`, `confidence_calibrated`,
`needs_human_review`, `method_version` and `produced_at`. An interpretive
label with no confidence and no review path becomes treated as a fact.

**Milestone 4.5.8 — Live validation plan.** ✅ *written, unauthorised*
`docs/LIVE_VALIDATION_PLAN.md`: five costed operations totalling **$0.42**,
each with its provider, actor, expected results, known limitations, what it
establishes and what it cannot. Includes pre-committed stopping rules and
the observation that the **cheapest** operation in the plan ($0.075, a
second pass a week later) is the most valuable, because it is the only one
that produces a repeated observation.

**Blocked on the owner, not on us.** The cap is `$0.00` and
`providers_authorised` is empty, so every path resolves to fixtures and
refuses rather than attempts. Authorising a provider and supplying
`APIFY_TOKEN` switches the same code paths to live collection with no code
change — which is what the fixture adapter subclassing the real one exists
to guarantee.

### Phase 6A — Hook Intelligence  **(delivered 2026-10-26)**

The first real feature extraction from actual video files, on the owner's
own 258 preserved Instagram publications. Brought forward ahead of the rest
of Phase 6 because it needs no competitor media, no paid authorisation and
no GPU — and because it is the one place where an extracted feature can be
tested against an independently measured outcome.

**Milestone 6A.1 — Deterministic opening measurement.** ✅
`shared/hookfeat.py`. ffmpeg + numpy + PIL only; **no OpenCV, scipy or
librosa added** — frame decoding, FFT phase correlation, Spearman
correlation and Benjamini-Hochberg are implemented directly, because one
correlation does not justify a dependency (CLAUDE.md §6). Runs on the
server where the media already is, so no file moved and no GPU was touched.

21 reproducible measurements per video: cut count and times, frame
difference, camera translation by phase correlation, brightness, contrast,
saturation, edge density, first sound, attack time, RMS, onset count,
spectral statistics, and visual/audio novelty against a later window.
**0.94 s per video.**

**Milestone 6A.2 — Versioned feature evidence.** ✅ Migration
`f1a6c8d27b93`. `video_features` keyed on (video, feature,
**method_version**), so reprocessing is additive by construction: a new
version writes beside the old rather than over it, and the two can be
compared. Verified live — one publication now carries both
`hook-speech-v1` and `hook-speech-v2` evidence.

A human correction lands in its own columns on the row it corrects.
Erasing the machine's value would make it impossible to ever measure how
often the machine was wrong, which is the thing corrections exist to
reveal.

**Milestone 6A.3 — Two detectors found wrong, by measurement.** ✅

| detector | claimed | truth | outcome |
|---|---|---|---|
| hand-built speech heuristic | speech in **12/12** | **0/12** | **deleted**, not tuned |
| Whisper on 3-second clips | 4 transcripts | **all 4 hallucinations** | fixed with a 15 s context window |

The speech heuristic combined spectral flatness, zero-crossing rate and a
voice-band ratio. Solo piano varies all three exactly as speech does, so it
carried no signal. Had it shipped it would have opened the ASR gate on
every video and written 12 invented transcripts into the database as
observations.

The Whisper failure is the more interesting one: on the 3-second window it
produced `'you'` ×3 and a *confident* Korean transcript
(`no_speech_prob` 0.089) on an Italian pianist. Whisper identifies language
from roughly 30 seconds of audio; on 3 seconds over music it guesses. Fixed
by transcribing a **15-second context window** and keeping only segments
overlapping the hook, plus a language-confidence gate and a repeated-token
filter.

**After the fix: 4 of 48 have speech, and all 4 transcripts match the
post's own caption** — independent evidence the creator wrote. 8
hallucinations rejected. Speech is near-absent from this corpus (8%), which
is itself the finding: speech-based hook features have no variance here.

**Milestone 6A.4 — Hooks versus retention.** ✅ `server/features/retention.py`

Outcome is `reels_skip_rate`. `ig_reels_avg_watch_time` is **not** used as
a completion fraction: it includes replays, and one real video averages
**3.38× its own duration**.

n = **48** — of 385 Instagram publications, 258 have a file and only 49
have a retention metric. That is a hard ceiling and processing more videos
cannot raise it.

15 pre-declared features, Spearman with Benjamini-Hochberg FDR control,
partial correlations against duration **and** publication age. Two survive:

| feature | rho | +duration | +duration+age | p adj | 95% CI |
|---|---|---|---|---|---|
| `text_region_score` | −0.563 | −0.566 | **−0.524** | 0.0005 | [−0.734, −0.324] |
| `edge_density` | −0.436 | −0.446 | **−0.408** | 0.0175 | [−0.646, −0.165] |

Negative means *less* skipping. They correlate only 0.42 with each other,
so they are largely independent. **These are hypotheses, not findings**:
n=48, one creator, correlational, and the text proxy's own precision is
0.89. Thirteen features showed nothing, which is reported rather than
buried.

**Milestone 6A.5 — Hook Intelligence interface.** ✅ `webui/hooks.js`.
Browse analysed videos, play exactly the measured 3 seconds, inspect every
feature with its method badge, uncertainty and provenance, correct a
classification, re-run extraction over a bounded scope, and read the
association table with its controls and caveats. A video with no features
says *not analysed* rather than showing blanks.

**Milestone 6A.6 — Found defect: the front end was never really parsed.**
✅ ADR 0006 chose a no-build SPA, so nothing parses the JavaScript before a
browser does. `node --check file.js` was being used as the gate and it is
**not sufficient** — without `--input-type=module` node parses a `.js` file
as a CommonJS script and misses errors the browser rejects. A real
SyntaxError in `webui/hooks.js` passed that check. `tests/server/
test_webui_syntax.py` now parses every module in module mode and also
verifies every named import resolves to a real export.

**Throughput, measured.** Deterministic 0.94 s/video (≈4 min for all 258);
speech 3.78 s/video (≈16 min for all 258). **Zero GPU time.** Both passes
are CPU-only on the server's 4 cores, which leaves the RTX 2070's 8 GB free
for the Phase 6B vision work that genuinely needs it.

### Phase 5 — Breakthrough Analysis at Scale

> Milestones 5.1, 5.2 and 5.4 were **delivered early in Phase 4**
> (`server/research/baseline.py`, `breakthrough.py`), because targeted
> creator research is not useful without them. What remains here is what
> needs a *corpus* rather than one creator.

The most distinctive feature in the product, and the easiest to get
scientifically wrong.

**Milestone 5.1 — Rolling baselines.** ✅ *delivered in Phase 4.* Local
trailing median/MAD in log space, with the window age spread reported as
the size of the confound that could not be removed.

**Milestone 5.2 — Change-point detection.** ✅ *delivered in Phase 4.*
CUSUM localisation plus a robust median significance test and a sustain
check.
*Remaining:* distinguishing **successive** breakthroughs from a single one.
The current detector reports the strongest boundary only.

**Milestone 5.3 — Cohort control.** Niche-wide movement over the same
period is computed and subtracted.
*Acceptance:* a month in which the whole niche jumped is attributed to the
platform, not to any creator.

**Milestone 5.4 — Evidence gating.** ✅ *delivered in Phase 4.* Status
defaults to `inferred_breakthrough`; promotion needs repeated observations
**and** follower history; `missing_evidence` names what is absent on every
inference.

**Milestone 5.5 — Creator timeline visualisation.** Publication history,
outliers against the rolling baseline, detected change points, and
subsequent performance — inline SVG, no charting dependency.
*Acceptance:* a human can see from the timeline whether a detected
breakthrough is plausible.

**Depends on:** Phase 4 corpus; **at least two capture days** (see below).

---

### Phase 6 — Multimodal Feature Extraction

Structured musical, visual, editing and language characteristics, per
`docs/PRODUCT_VISION.md` §3.D. Uses the Phase 0B-measured configuration and
the per-modality batch design (ADR 0002, ADR 0004).

**Milestone 6.1 — Feature vocabulary** (`docs/FEATURE_SCHEMA.md`).
**Milestone 6.2 — Extraction jobs** on the existing queue; one modality in
VRAM at a time.
**Milestone 6.3 — Provenance and confidence** on every feature: method,
model, version, confidence, extracted-at. Re-extraction adds rows.
**Milestone 6.4 — Human correction**, stored alongside the machine value
and reused as labelled evaluation data.
*Acceptance:* an AI classification is never stored as ground truth; a
correction never overwrites a machine value; low-confidence features are
excluded from modelling rather than averaged in.

---

### Phase 7 — Comparative Analytics and Temporal Intelligence

**Milestone 7.1 — Three-way comparison** of viral, moderately successful
and ordinary publications, with all three strata preserved by the sampler.
**Milestone 7.2 — Confound handling:** creator baseline, account size,
platform, niche, age, observation date, seasonality, sampling weights,
correlated features.
**Milestone 7.3 — Predictive model v1** under the §12.3 protocol (V1–V5
reported **separately**, never averaged) against both baselines of §12.4.
**Milestone 7.4 — Temporal intelligence:** recency weighting, rolling
windows, emerging/declining detection, change points, historical-vs-current
comparison, drift-triggered recalibration, and the time-travel view.
**Milestone 7.5 — Attribution discipline:** distinguish algorithm change
from audience shift, sampling change, competition and creator behaviour.
*Acceptance:* a no-beat result is reported plainly as a valid outcome; a
2023 strategy is not recommended in 2026 merely because its videos
accumulated views; an expired finding is demoted, not silently reused.

---

### Phase 8 — Evidence-Based AI Creative Strategist

Five layers, deliberately separated (`PRODUCT_VISION.md` §3.G):
extraction → statistical modelling → evidence retrieval → LLM strategist →
feedback learning.

**Milestone 8.1 — Evidence retrieval** over findings and comparable videos
(pgvector in the existing Postgres).
**Milestone 8.2 — Strategist** producing the full structured brief:
repertoire, concept, hook, spoken words, camera, lighting, duration,
pacing, on-screen text, caption, hashtags, cover, platform variants,
posting window, comparables, uncertainty, alternatives for testing.
**Milestone 8.3 — Conversational refinement** against stored artistic
identity, equipment and repertoire.
**Milestone 8.4 — Evidence lineage:** `recommendation_evidence` rows are
mandatory.
*Acceptance:* **no claim without a traceable statistic**; the LLM never
originates a number; nothing is labelled `established_finding` unless it
survived §12.3 and beat both baselines. **No model is trained from
scratch**; fine-tuning only when validated data justifies it.

---

### Phase 9 — Live Experiment and Closed-Loop Learning

The MVP's actual endpoint, unchanged from the previous numbering (old Phase
8): a pre-registered own-account experiment testing whether recommendations
*improve outcomes*, with its stated power limits.

Adds the closed loop: *evidence → recommendation → brief → variant →
publication → observed performance → updated model*, stored as
relationships rather than conventions.
*Acceptance:* predicted vs observed compared for content the system
actually advised; pre-registered stopping rule honoured; an inconclusive
result stays inconclusive.

---

### A dependency that gates Phases 5, 7 and 9

**Every snapshot in the database was captured on one day** (2026-10-24), at
publication ages from 1 to 1357 days. So no genuine `views@7d` or
`views@30d` exists for any current publication, and none may be
reconstructed — `server/analytics/fixed_age.py` returns `None` with a
reason rather than interpolating.

Fixed-age analysis becomes possible only as the daily collector accumulates
history: `views@7d` from ~2026-11-02 for publications posted from now,
`views@30d` from ~2026-11-25, `views@90d` from ~2027-01-24. For the
existing archive the only sound comparison is §5.4 Strategy A — compare
mature publications within an age band — never a reconstructed anchor.
Full detail in `docs/phase3-data-readiness.md`.

### Phase 10 — Continuous Learning Loop *(post-MVP)*

> Renumbered from Phase 9. Its **drift detection and recalibration triggers
> moved forward into Phase 7** (temporal intelligence) and its
> **prediction-vs-outcome comparison into Phase 9** (closed loop), because
> the vision document makes both load-bearing rather than post-MVP. What
> remains here is the fully automated retraining cadence.
Scheduled retraining, drift detection on model performance over time,
automated prediction-vs-outcome tracking at scale, recommendation-
effectiveness evaluation.
**Acceptance:** a full automated cycle — recommend → publish → measure →
compare → retrain — runs without manual intervention, with drift detection
firing correctly on a synthetic degradation test.

### Phase 11 — Trend Intelligence *(post-MVP)*

> Renumbered from Phase 10. Its **emerging/declining pattern detection
> moved forward into Phase 7**; what remains is cross-niche trend
> discovery beyond the owner's own niche.
Growth-rate tracking of hashtags/audio/formats; early-emergence detection vs
already-saturated trends.
**Acceptance:** a historical backtest shows ≥1 real trend flagged before an
operationally-defined saturation point.

### Phase 12 — Creation, Publishing & Repurposing *(explicitly future)*

> Renumbered from Phase 11. Its **generative recommendation content moved
> forward into Phase 8** (the AI creative strategist), which the vision
> document makes the flagship feature rather than a future extra. What
> remains here is content *production*, scheduling and actual publishing —
> still explicitly post-MVP.

Everything in this phase is **out of MVP scope and must not be built early**.
It is recorded here so the architecture does not foreclose it — the
`DataSourceAdapter` seam, the per-video feature store and the evidence-tagged
recommendation records all exist partly to make this reachable later.

Each item carries the same discipline as the rest of the plan: a
recommendation is only ever as strong as the evidence behind it, and anything
generative must label itself as a hypothesis until measured.

**11.1 Generative assistance**
- AI-generated **hooks, captions, hashtags and content ideas**, grounded in
  the features that actually correlated with performance in the owner's own
  corpus — not generic LLM advice, which is explicitly the baseline the
  models must beat (§12.4).
- Every generated suggestion carries its evidence class (`established
  finding` / `hypothesis`) like any other recommendation.

**11.2 Timing and audience targeting**
- Evidence-based **publishing times**, derived from the posting-time features
  already collected, at fixed metric ages so the comparison is valid (§5.4).
- **Geographic targeting** where a platform actually supports it. Note the
  known ceiling: geographic data is sparse in the collected fields, so this
  may remain unanswerable — it is a research question, not a commitment.

**11.3 Calendar and scheduling**
- A **visual content calendar** across platforms.
- **Cross-platform scheduling** with platform-specific variants of the same
  underlying idea.

**11.4 Automated publishing**
- **TikTok** via the Content Posting API — requires the app audit deliberately
  avoided during Phase 2 (demo video, privacy policy, ToS, use-case review;
  1–3 weeks).
- **Instagram** via the Content Publishing API on the Facebook Login path.
- Publishing is the point at which this system stops observing and starts
  acting, so it needs a hard confirmation step and a spending/rate guard
  before anything goes live.

**11.5 AI-assisted editing and optimisation**
- Automatic clip selection, caption/subtitle burn-in, multiple variants of a
  cut, and optimisation against the features that predicted performance.
- Constrained by the measured GPU reality (ADR 0004): generation is far more
  expensive than analysis, so this likely needs batching or rented compute.

**11.6 Intelligent repurposing engine**

The most distinctive item, and the one the data model is already shaped for:

- Identify **successful older videos** worth reposting, and
  **underperforming hidden gems** — content whose features resemble known
  winners but which underperformed, suggesting distribution rather than
  quality was the limiting factor.
- Suggest **specific improvements** tied to the features that differ from
  comparable winners.
- Produce **alternative versions** (recut, rehooked, recaptioned).
- **Measure the result of reposting** against the original, as a paired
  comparison at matched metric ages.

This is a natural experiment: the same underlying content, published twice
with a deliberate change between them. It is the closest this project can get
to a controlled trial without a large sample, and it reuses the Phase 8
pre-registration machinery rather than needing new methodology.

**11.7 Continuous learning loop**
- Close the circuit: analysis → creation → publishing → measured performance
  → model update → better analysis.
- Phase 9's feedback loop is the foundation; this extends it to cover content
  the system itself generated, which is where the causal question gets
  sharpest and where self-confirmation bias is most dangerous. Generated
  content must be tagged as such so its performance can be analysed
  separately rather than quietly inflating the model's apparent accuracy.

---

## 8. Testing & Validation Requirements

- **Unit tests** for every adapter against recorded fixtures — never live
  platform APIs in CI.
- **Permanent regression tests** from Phase 1's four adversarial job tests
  (requeue, stale-token rejection, replay idempotency, bounded retry).
- **Statistical invariants, automated:** a CI assertion that no feature used
  by the recommendation model depends on information unavailable at
  prediction time (§12.2); a check that any training set drawn from
  `analysis_samples` carries its sampling weights.
- **Human-in-the-loop gates:** Phase 0B (VLM output usefulness), Phase 4
  (label sanity), Phase 7 (recommendation specificity). Automated metrics
  cannot validate these.
- **Benchmark-before-scale:** no throughput or cost figure may be written as
  fact until measured on the real hardware and corpus; everything else stays
  tagged `[ASSUMPTION]`.

---

## 9. Proposed Directory Structure

```
ViralLab/
├── CLAUDE.md
├── DEVELOPMENT_PLAN.md
├── README.md
├── .env.example
├── config/
│   └── settings.yaml            # spending caps, tier budgets, provider toggles
├── probes/                       # Phase 0A/0B throwaway scripts (kept for reproducibility)
│   ├── data_access/
│   └── local_ai/
├── server/                       # Ubuntu
│   ├── api/
│   ├── collectors/
│   │   ├── base.py               # DataSourceAdapter interface
│   │   ├── tiktok_official.py
│   │   ├── instagram_official.py # Facebook Login path
│   │   ├── media_ingest.py       # watched folder for your own masters
│   │   └── discovery/
│   ├── scheduler/
│   ├── queue/                    # SKIP LOCKED, lease tokens, heartbeat, reaper
│   ├── db/                       # SQLAlchemy models + Alembic migrations
│   ├── analytics/                # baselines, outliers, stratified sampler
│   ├── screening/                # Tier 1 metadata features (CPU)
│   ├── modeling/                 # training, validation suite, model registry
│   ├── recommendations/
│   ├── experiments/              # Phase 8 pre-registration & analysis
│   ├── trends/                   # Phase 10
│   └── dashboard/                # Jinja2 + HTMX
├── gpu_worker/                   # Windows
│   ├── worker.py                 # poll / lease / heartbeat / execute / submit
│   ├── pipelines/
│   │   ├── transcription.py
│   │   ├── vision.py
│   │   ├── audio.py
│   │   ├── cv_classic.py
│   │   └── ocr.py
│   └── config.yaml
├── shared/
│   ├── schemas/                  # Pydantic job/feature/metric contracts
│   └── utils/
├── scripts/
├── tests/
│   ├── server/ · gpu_worker/ · shared/
└── docs/
    ├── adr/                      # architecture + API-verification records
    └── benchmarks/               # 0A, 0B, and later measurement reports
```

---

## 10. Cost & Throughput Projections — **UNVERIFIED**

> **Phase 0A/0B replaced these projections with measurements** (2026-10-23).
> GPU time, per-stage cost, VRAM and provider cost are now **measured on the
> real hardware and real provider runs** — see `docs/benchmarks/0A-data-access.md`
> and `docs/benchmarks/0B-local-ai.md`.
>
> Two caveats remain. **Storage is `[PROVISIONAL]`**: measured across 14
> files with a 9.6× spread in MB/s, from one niche and one platform pair —
> provision against the p90 column. And the GPU figures assume the
> **per-modality-batch** job design (§4.2); a per-video design would be
> roughly 5× worse, because cold load dominates.

Because analysis is tiered (§5.2), cost scales with **tier mix**, not corpus
size. Illustrative mix: Tier 1 on 100%, Tier 2 on ~10%, Tier 3 on ~1%.

**MEASURED in Phase 0B** (Tier 2 = 11.4 s/video, Tier 3 = 29.7 s/video,
cold loads amortised over a 100-video batch; storage median 2.67 MB/file,
p90 5.14 MB):

| Corpus | Tier 2 @10% | Tier 3 @1% | **Total GPU time** | Discovery cost *(measured $0.30–3.00/1k)* | Media storage *(median / p90)* |
|---|---|---|---|---|---|
| 1,000 | 100 | 10 | **~24 min** | $0.30–3.00 | 3 / 5 GB |
| 10,000 | 1,000 | 100 | **~4.0 hrs** | $3–30 | 27 / 51 GB |
| 100,000 | 10,000 | 1,000 | **~40 hrs (~1.7 days)** | $30–300 | **267 / 514 GB** |

The VLM is **79% of Tier 2** and **91% of Tier 3** cost, so **frame count is
the only real cost lever** in the pipeline.

**Tiering changed the feasibility picture, and the measurement confirmed it.**
Revision 1 projected *6–17 days* at 100k by assuming full multimodal analysis
of every video. Revision 2's tiered estimate of "under two days" is now
**measured at ~40 hrs** — tractable for an occasionally-on machine.

Storage remains `[PROVISIONAL]`: 14 files, one niche, one platform pair, with
a **9.6× spread** in MB/s. Provision against the p90 column, not the median.

Other items:
- Official TikTok Display API / Instagram Graph API: **$0** at this scale
  (both free and rate-limited rather than volume-priced) — this one *is*
  verified.
- Electricity: ~300 W under load **[ASSUMPTION]**; at an **unknown local
  tariff** (not supplied), 40 hrs ≈ 12 kWh ≈ low single-digit currency
  units. Negligible against GPU wall-clock time.
- Storage is cheap and purgeable: once Tier 2/3 features are extracted, raw
  media can be deleted under a configurable retention policy, with
  `media_status` tracking what was purged.
- Optional cloud AI: a **hard monthly cap** in `config/settings.yaml`
  (suggested $20–50), never silently exceeded.

---

## 11. Risks & Open Questions

**Risks**
- **API/ToS churn** — Revision 2 already had to correct three facts that
  changed or were misreported within ~18 months (IG metric deprecation, the
  two IG login paths, TikTok media availability). Mitigated by the
  `DataSourceAdapter` seam and the mandatory verification rule, but
  re-verification is a recurring cost, not a one-time task.
- **Small own-account sample** — your personal video count may be too small
  to learn from alone, making cross-creator transfer necessary; whether that
  transfer works is open (below) and is now directly measured by §12.3's V4.
- **Survivorship / platform-confound bias** — the platform decides
  distribution for reasons ViralLab cannot observe. Findings are
  correlational; stratified sampling (§5.2) mitigates the worst selection
  bias but cannot eliminate this.
- **Experiment under-power** — Phase 8 will likely be inconclusive at MVP
  sample sizes (§7 Phase 8). Stated up front so an inconclusive result is
  not quietly reinterpreted as success.
- **Windows availability** — GPU backlog must be dashboard-visible or it
  silently accumulates.
- **Media availability for your own TikToks** — depends on you retaining
  masters (§2.1). Any gap in your archive is a permanent gap in Tier 2/3
  coverage of your own history.

**Minor outstanding issues** (recorded, deliberately not fixed)
- `GET /status/queue` requires no auth — read-only and tailnet-only, but it
  discloses queue depth to anything already on the tailnet.
- `spending.monthly_cap_usd` exists in config but nothing reads it yet.
  **Must be wired in before any paid provider is enabled** (CLAUDE.md §7).
- The reaper logs via the `virallab.api` logger, which uvicorn does not
  configure, so its lines are absent from `logs/api.log`. The loop itself is
  verified to run (`scripts/provision/verify_reaper.sh`).
- Local dev uses portable PostgreSQL 17 (winget needs UAC); pgvector is
  unavailable in those Windows binaries. Fine until Phase 5; Ubuntu has it.
- Windows dev runs PG 17 while the server runs PG 18. The suite passes on
  both; worth keeping in mind for migrations.

**Open questions**
0. **How are TikTok publications tied to their Instagram counterparts with
   content-level evidence?** Currently they are not: TikTok returns no media,
   and the cover image was implemented, hashed for all 234 publications, and
   then measured and rejected (median Hamming 16/64; distinctive on 1 of 234;
   2 fps re-sampling improved 3 of 14). The 201 cross-platform pairs rest on
   duration + date + caption and are stored as `proposed`, never confirmed.
   → What would settle it: the owner's own export logs or editing project
   files, or publishing one test video and logging the field set returned for
   it. Not blocking — a `proposed` pair already lets both platforms' metrics
   sit side by side without asserting they describe the same content.
0a. **Why does Instagram withhold `media_url` for exactly 28 Reels?** All 28
   report `media_type=VIDEO`, `media_product_type=REELS` and a
   `thumbnail_url`, while `media_url` is absent from the response entirely
   `[PRIMARY, empirical]`. Licensed audio is the common explanation but is
   `[SECONDARY]` and is not asserted. → Settled by comparing those
   publications' audio attribution against the rest of the corpus. Changes
   nothing operationally: the media is unavailable either way.
0b. **Can two different takes of the same work be distinguished?** The
   fingerprint benchmark's negatives are all unrelated performances. No
   labelled example exists of two separate performances *of the same piece*
   by this performer, which is the most plausible remaining confusion.
   → Needs examples the owner identifies.
1. Does a model trained largely on *other* creators' videos transfer to
   predicting *your* relative performance? → §12.3 test V4, Phase 6.
2. Does classical-piano content carry enough niche-specific structure
   (repertoire, tempo, dynamics, framing conventions) that generic
   multimodal features miss it, requiring domain-specific extractors?
   → flagged if Phase 6 performs notably worse on your content than on the
   broader niche.
3. Do recommendations **causally** improve outcomes? → Phase 8, with the
   power caveat. Not claimed before then, under any phrasing.
4. ~~Throughput and quality on this card?~~ **ANSWERED (0B).** Whisper
   `small` int8 **258× realtime**; Qwen2.5-VL Q4 **2.25 s/frame** warm,
   **44.7 s** cold load, **7634 MiB** peak (93% of the card). VLM quality
   passes qualitative review; ASR does not — see 6.
5. Is any provider's niche coverage complete and reliable enough, and does
   any return **media** as well as metadata? → Phase 0A gate.
5a. ~~Does the TikTok Display API work in a sandbox, or is a production
   audit required?~~ **ANSWERED (2026-10-24): sandbox works.** A sandbox app
   with `user.info.basic`/`user.info.stats`/`video.list` returned the
   owner's full 234-video catalogue with metrics. No audit, no demo video.
   Sandboxes also issue their own `sb…`-prefixed credentials, which TikTok
   does not document.
6. ~~Does Whisper degrade on piano-performance audio?~~ **ANSWERED (0B):
   yes, badly.** Non-trivial transcript on only **1–2 of 14** videos; on
   music-only audio it **hallucinates** ("Thank you.", "Music", "Hello, I am
   a" repeated) instead of returning empty. Required: gate ASR behind speech
   detection and store NULL, never model output; treat **VLM-read on-screen
   text as the primary text channel** for this niche.
7. **By what age have views substantially stopped accumulating?** §5.4's
   Strategy A depends on it, and TikTok is known to resurface old content.
   Testable for free on your own account once Phase 2 has daily snapshots:
   plot the fraction of 90-day views reached by day 7 and day 30. Until
   then the anchor age is provisional.
8. ~~Does cold-load time dominate per-video cost?~~ **ANSWERED (0B): yes,
   5:1.** VLM cold load **44.7 s** vs **9.0 s** inference for a 4-frame
   Tier 2 video. **Job granularity becomes per-modality-batch**, per-video
   idempotency keys retained; ADR required before the Phase 1 schema.
9. **Is any provider's `posted_at` trustworthy?** → Phase 0A blocking gate.
   A provider that returns a scrape time dressed as a post date makes
   fixed-age comparison impossible and is unusable at any price.

---

## 12. Modeling Methodology

### 12.1 Target
A **relative, age-anchored** performance index —
`log(views@30d / creator_rolling_median_views@30d)` or a within-creator
percentile rank computed at the same anchor — never raw view count and
never a mixed-age comparison (§5.4). This controls the two dominant
confounds, audience size and exposure time, and matches what you can act on
("better than my own normal, at the same age").

Both the numerator and the denominator must use the **same anchor**. A
30-day value compared against a baseline built from mature videos of
unknown age is the same error one level up, and it is easy to introduce
accidentally when computing creator baselines.

### 12.2 Feature eligibility
Only features knowable **before or at publication** may predict for the
recommendation model: hook type, visual/audio features, caption, hashtags,
posting time, creator history. Post-publication signals (completion rate,
first-hour velocity, final metrics) are either the **target** or belong to a
separate "early virality signal" model — never both feature and target in
one model. Enforced by an automated CI check (§8).

### 12.3 Validation suite — all four tests required
| Test | Design | Answers |
|---|---|---|
| **V1 Chronological** | train on videos published before *T*, test strictly after | Does it hold forward in time, as deployment requires? |
| **V2 Creator-grouped** | `GroupKFold` by creator; no creator appears in both train and test of a fold | Is it learning content features, or memorising creator-level quirks? |
| **V3 Unseen-creator** | entirely held-out creators | Does it generalise to a creator never seen in training? |
| **V4 Transfer-to-me** | train with **all of your own videos excluded**, then evaluate specifically on your videos | **Does niche knowledge transfer to your account?** — the project's central risk |
| **V5 Walk-forward** | expanding-window retraining across the timeline | Does performance degrade as trends shift (drift)? |

V1–V4 are reported with uncertainty intervals, each as a separate line in
the Phase 6 report. V4 failing while V1–V3 pass is a specific, actionable
diagnosis (the niche model doesn't transfer to you) and must not be averaged
away into a single headline metric.

### 12.4 Required baselines
Any claim of model value must beat **both**:
1. **Predict the creator's own rolling median** — tests whether the model
   adds anything over "nothing changes."
2. **Generic AI content advice**, produced without ViralLab's data pipeline —
   tests whether multimodal statistical modeling adds anything over what an
   off-the-shelf LLM would say for free.

### 12.5 Sampling correction
Training sets drawn from `analysis_samples` carry inverse-probability
weights (§5.2). Unweighted training on a stratified sample silently
misstates base rates.

### 12.6 Age anchoring is an automated invariant
A CI check asserts that every metric entering labeling, modeling, or
experiment analysis carries an explicit anchor age, and that no comparison
mixes anchors. Mixed-age comparison is the easiest invalidating mistake in
this project to make silently and the hardest to notice in a result, so it
is enforced in code rather than left to discipline.

---

## 13. Completion Criteria Summary

| Phase | Done when |
|---|---|
| **0A** | All four gate conditions met (completeness incl. 100% of BLOCKING fields, timestamp cross-check, cost, ToS); media availability answered in writing |
| **0B** | Measured **total** cost (load/download/decode/inference/Tier 3/storage) + VLM usefulness pass; all `[ASSUMPTION]`s replaced; config and job-granularity ADRs recorded |
| 1 | ✅ **DONE 2026-10-24** — round-trip over TLS + all four adversarial tests, verified across the real two machines (11/11) |
| 2 | 7 days unattended own-account collection; masters linked; IG media captured before URL expiry |
| 3 | ≥1,000 niche videos ingested, real billed cost logged, re-runs idempotent |
| 4 | ≥85% label agreement on 50-video sample; sampler produces all three strata with recorded weights |
| 5 | Tier 1 corpus-wide; 100 Tier 2 + ~10 Tier 3 unattended; kill-recovery clean; throughput matches 0B |
| 6 | All four validation tests reported with intervals; explicit beat/no-beat vs both baselines |
| 7 | ≥10 recommendations reviewed, majority actionable, every claim evidence-classed |
| **8** | ≥20 pre-registered posts (≥10/arm) analysed with effect size **and** interval; verdict stated honestly |
| 9 | Automated recommend→measure→retrain cycle; drift detection fires on synthetic degradation |
| 10 | Backtest flags ≥1 trend pre-saturation |
| 11 | Not scoped — future planning cycle |

---

## Sources

### [PRIMARY] — platform-official; citable as fact
- [Instagram Platform overview — Instagram Login vs Facebook Login](https://developers.facebook.com/docs/instagram-platform/overview/)
- [Instagram Media Insights reference — `views`/`reach`/`saved`/`shares`, 48h delay, 2-year retention](https://developers.facebook.com/documentation/instagram-platform/reference/instagram-media/insights)
- [Graph API v22.0 changelog — Apr 2025 deprecation of `plays`/`impressions` et al.](https://developers.facebook.com/docs/graph-api/changelog/version22.0/)
- [IG Media reference](https://developers.facebook.com/docs/instagram-platform/reference/instagram-media/)
- [TikTok Video Object fields — no media URL, no `collect_count`](https://developers.tiktok.com/docs/en/tiktok-api-v2-video-object)
- [TikTok List Videos](https://developers.tiktok.com/docs/en/tiktok-api-v1-video-list)
- [TikTok Display API — Get Started](https://developers.tiktok.com/docs/en/display-api-get-started)
- [TikTok Research Tools: Access and Eligibility — non-commercial requirement](https://developers.tiktok.com/products/research-api/)
- [Apify pricing — Free plan, $5 credit, no card](https://apify.com/pricing)

### [SECONDARY] — provisional; re-verify before relying on it
- [TikTok Content Posting API audit process](https://developers.tiktok.com/docs/en/content-posting-api-get-started) *(the page is official; the 1–3 week timeline and rejection-rate expectation are not — those came from third-party write-ups)*
- [HiQ Labs v. LinkedIn — case summary, not the docket](https://en.wikipedia.org/wiki/HiQ_Labs_v._LinkedIn)
- [Meta v. Bright Data — case summary, not the docket](https://www.zyte.com/blog/california-court-meta-ruling/)
- [Apify TikTok actor per-result pricing](https://apify.com/marsultor/tiktok-post-scraper)
- [Qwen VRAM requirements](https://gigagpu.com/qwen-vram-requirements/)
- [Whisper RTF by GPU](https://gigagpu.com/whisper-large-v3-rtf-by-gpu/)
- [Instagram Insights field-change tracker](https://docs.supermetrics.com/docs/instagram-insights-updates) *(superseded by the primary changelog above; retained for its field-level history)*
