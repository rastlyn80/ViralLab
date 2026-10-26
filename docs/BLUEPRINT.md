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
| Own Instagram corpus | 385 publications, **258 with media**, **48 with retention metrics** | `[PRIMARY]` |
| Own TikTok corpus | 234 publications, **225 with media** (5 geo-blocked, 1 deleted, 3 transient) | `[PRIMARY]` |
| Competitor corpus | **53 creators, 60 publications**, TikTok + Instagram, $0.09 spent | `[PRIMARY]` |
| Deterministic hook features | 21 visual/audio measures on 258 videos + background structure on 48 | `[PRIMARY]` |
| OCR | RapidOCR on 258; real text measurement; **no OCR measure relates to retention** | `[PRIMARY]` |
| Speech | Whisper small, gated; speech present in 4 of 48 | `[PRIMARY]` |
| Local vision | Gemma 3 4B on the RTX 2070, 7.9 s/video, $0; **one reliable field of eight** (`text_overlay_present`) | `[PRIMARY]` |
| Retention science | `text_region_score` −0.563 vs skip rate (q<0.001); it measures background structure, not text | `[PRIMARY]` |
| Spending control | Per-category caps enforced, runtime authorisation separated from code, real costs captured | `[PRIMARY]` |
| Credentials dashboard | Write-only, whitelisted, live | `[PRIMARY]` |
| Competitor media | **None downloaded, none analysed** | — |
| Creator report, comparisons, opportunities, strategist | **Not started** | — |

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

The owner has run a **$1/day Instagram ad that rotates to each new post**
for several months. Every recent Instagram metric is therefore a blend of
paid and organic exposure, and the blend differs per video.

### 1.1 Why this matters more than it looks

Views are the obvious casualty, and the least important one — ViralLab
already refuses to rank on raw views. The real damage is to **per-viewer
behaviour metrics**, which the retention science depends on:

- `reels_skip_rate` and `ig_reels_avg_watch_time` are computed over
  everyone who saw the video, and an ad-delivered cold viewer behaves
  differently from a follower.
- The ad rotates on each new post, so a video's paid exposure is roughly
  **proportional to the time until the next post**. That makes ad exposure
  a function of posting rhythm — and anything correlated with posting
  rhythm inherits the contamination.
- The ad also recruited followers over months, so the organic audience
  itself drifts in composition. A slow confound, handled by time controls.

### 1.2 First test, already run `[PRIMARY, 2026-10-26]`

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

`[SECONDARY — verify at developers.facebook.com before writing code]`
A boosted Instagram post becomes an ad with its own `ad_id` in the Marketing
API, whose creative references the organic media. Ads Insights report that
ad's own impressions, reach, video plays and watch-time distribution
(`video_p25_watched_actions` … `video_p100`). If that holds, then for every
boosted post:

    organic ≈ media_insights(total) − ads_insights(paid)

giving organic views, organic reach, and an organic watch-time profile.
Skip rate cannot be decomposed this way unless the ad reports an
equivalent, which is `[OPEN]`. This requires the owner's Ads account to be
reachable through the existing Facebook Login token with `ads_read` scope —
`[OPEN]`, and the first thing M1 checks.

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
