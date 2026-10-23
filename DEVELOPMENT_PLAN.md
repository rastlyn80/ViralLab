# ViralLab — Development Plan

**Revision 3** — Phases 0A and 0B executed; both gates passed (2026-10-24)
Status: **0A GATE PASSED** (incl. ToS review) · **0B GATE PASSED** —
no application code yet; probe scripts only. Phase 1 awaits owner approval.
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
collectors: OAuth, token refresh (IG's 60-day token must be refreshed before
expiry), scheduled daily pulls into `metrics_snapshots`. Prompt download of
IG `media_url` before expiry. **Media-ingest watcher** for your own TikTok
masters, with fingerprint linking and manual-link fallback in the dashboard.
Empirically confirm the actual returned field set against the docs (e.g.
whether TikTok returns any saves field) and record the verification in an
ADR.
**Acceptance:** complete historical video list + metrics for both your
accounts; your own masters linked to their metadata rows; scheduled
collection runs unattended for **7 consecutive days** with no manual
re-auth, no crash, and no missed IG media download.

### Phase 3 — Niche Discovery Ingestion
Build the discovery adapter against whichever provider won Phase 0A, plus
the official IG Hashtag Search / Business Discovery foothold where it adds
value. Normalize into the shared schema.
**Acceptance:** 1,000–5,000 videos from one real niche ingested, with
per-provider cost **logged from actual billing**, and ingestion idempotent
across re-runs (re-running a collection does not duplicate rows).

### Phase 4 — Baselines, Outlier Labeling & Sampling Design
Rolling per-creator median/MAD baselines; three-way labeling; the stratified
Tier 2/3 selector writing `analysis_samples` with sampling probabilities.
**Acceptance:** you manually review a random 50-video label sample and
confirm ≥85% agreement (a sanity check, not a scientific claim); the sampler
demonstrably produces a sample containing all three performance strata in
the intended proportions; methodology written up.

### Phase 5 — Progressive Analysis Pipeline
Tier 1 on Ubuntu for all videos; Tier 2 and Tier 3 as GPU jobs using the
Phase 0B-chosen configuration.
**Acceptance:** Tier 1 across the full ingested corpus; **100 videos through
Tier 2** and ~10 through Tier 3, unattended; a mid-batch worker kill is
recovered with no data loss and no duplicate feature rows; measured
throughput matches Phase 0B's benchmark within a stated tolerance (if it
doesn't, the cost model is re-derived before scaling).

### Phase 6 — Predictive Modeling v1
LightGBM on Tier 1 + Tier 2 features with inverse-probability weights, under
the four validation tests of §12.3, against the two required baselines of
§12.4.
**Acceptance:** a validation report giving results for **all four** tests
(chronological, creator-grouped, unseen-creator, and transfer-to-your-account)
with uncertainty intervals, plus an explicit beat/no-beat verdict against
both baselines. **A no-beat result is a valid, reportable outcome** — it
must be stated plainly, and any recommendation surfaced afterwards must be
labeled as not-yet-validated.

### Phase 7 — Recommendation Generation
Model feature importances + pgvector nearest-neighbour retrieval of similar
high-performing videos (filtered to your niche and repertoire style) →
structured recommendations with per-claim evidence classes
(`established finding` / `hypothesis`).
**Acceptance:** ≥10 recommendations for real upcoming content reviewed by
you; a majority rated specific enough to act on; every claim carries an
evidence class and a traceable supporting statistic.

### Phase 8 — Live Own-Account Experiment *(new; now inside the MVP)*
A **pre-registered, paired, interleaved A/B** on your real TikTok and
Instagram accounts:
- Arms: **ViralLab-guided** posts vs **control** posts made by your normal
  process. Assignment **randomized and interleaved** in time.
- Each post's predicted relative-performance is **pre-registered in
  `experiments` before publishing** — no post-hoc fitting.
- Analysis: paired non-parametric test (e.g. Wilcoxon signed-rank) on the
  relative-performance index, with a confidence interval, plus the
  prediction-vs-actual calibration from `outcomes`.
- **Interleaving, not before/after.** A before/after comparison would be
  badly confounded by your own skill improvement over time and by account
  growth shifting the baseline. Interleaving controls both.
- **Stated power limitation, up front:** with n≈20–40 posts over the MVP
  window, only a **large** effect is detectable. The expected honest output
  is a directional signal with a confidence interval that may well include
  zero. A pre-registered stopping rule governs any early read, and posts
  keep accumulating past the MVP.

**Acceptance:** ≥20 posts (≥10 per arm) published under the pre-registered
protocol, analysed and written up with effect size **and** interval — and
the write-up explicitly distinguishes "recommendations measurably improved
outcomes," "no detectable effect at this sample size," and "inconclusive."
Declaring success requires the first; the MVP is complete either way,
because the MVP's job is to *run the test honestly*, not to pass it.

> **MVP acceptance = Phases 0A–8 each individually accepted.** This answers
> the brief's five MVP questions plus the one Revision 1 deferred: whether
> recommendations actually improve outcomes.

### Phase 9 — Continuous Learning Loop *(post-MVP)*
Scheduled retraining, drift detection on model performance over time,
automated prediction-vs-outcome tracking at scale, recommendation-
effectiveness evaluation.
**Acceptance:** a full automated cycle — recommend → publish → measure →
compare → retrain — runs without manual intervention, with drift detection
firing correctly on a synthetic degradation test.

### Phase 10 — Trend Intelligence *(post-MVP)*
Growth-rate tracking of hashtags/audio/formats; early-emergence detection vs
already-saturated trends.
**Acceptance:** a historical backtest shows ≥1 real trend flagged before an
operationally-defined saturation point.

### Phase 11 — Creation & Publishing *(explicitly future)*
AI-assisted clip selection, caption/subtitle generation, content calendar,
TikTok Content Posting API integration (needs the §2.1 audit),
post-publish auto-tracking. Not designed in detail here.

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

**Open questions**
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
| 1 | Round-trip over TLS + all four adversarial job tests pass |
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
