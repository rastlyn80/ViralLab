# CLAUDE.md — Permanent Development Instructions for ViralLab

**Revision 4** (2026-10-27, push rule added after M3). These rules apply to every session,
indefinitely, and override general habits and defaults.

## Project context

ViralLab is a local-first social-media intelligence and content-optimization
platform, initially a private tool for a professional concert pianist, with
an architecture that must stay niche-agnostic for possible future
commercialization. Python-first, two machines (always-on Ubuntu server + an
occasionally-on Windows **RTX 2070 SUPER** box, 8 GB VRAM). Full detail lives in
`DEVELOPMENT_PLAN.md` — read it before proposing work.

---

## 1. Workflow rules

1. **Follow `DEVELOPMENT_PLAN.md`.** Work on **one phase at a time**. Do not
   start Phase N+1 before Phase N's acceptance criteria are met and
   demonstrated.
2. **Phases 0A and 0B are hard gates.** No infrastructure, schema, or
   service code is written until both feasibility probes pass their gates.
   Probe code lives in `probes/` and is explicitly throwaway.
3. **Update `DEVELOPMENT_PLAN.md` after each completed phase** — record what
   actually happened, and replace each `[ASSUMPTION]` tag with the
   measurement that superseded it.
4. **Request approval before major architectural changes.** Swapping the job
   queue, database, model family, provider, or deployment topology is a
   discussion, not a unilateral commit.
5. **Explain architectural decisions** as they are made; record non-obvious
   ones as an ADR in `docs/adr/`.
6. **Test before claiming completion.** "It should work" is not completion.
   Run it and show the output.
7. **Preserve working functionality.** No refactor that breaks a passing
   phase without explicit sign-off.
8. **Document unresolved problems** in `DEVELOPMENT_PLAN.md` §11, not in
   chat history.
9. **Always push what you commit.** A commit is not the end of the task;
   `git push` is. Do not wait to be asked, and do not treat pushing as a
   separate decision needing its own approval — committing without
   pushing leaves the owner's only copy on one machine, which is the
   outcome this rule exists to prevent. If the work is on a branch,
   push the branch; if the branch is a clean fast-forward of `main` and
   nothing says otherwise, it goes to `main`. Say what was pushed and
   to where. The one thing that still stops you is a genuine conflict
   or a force-push — those are a question, not a reflex.

---

## 2. API verification rules *(new in Rev 2 — three facts were wrong in Rev 1)*

Revision 1 of the plan carried three incorrect API claims sourced from
third-party blogs: Instagram metrics that had been deprecated 18 months
earlier, a conflation of the two Instagram login paths, and an assumption
that TikTok's Display API returns downloadable media (it does not). Hence:

1. **Verify every API capability against the platform's own official
   developer documentation before writing integration code.** Blog posts,
   tutorials, SDK wrappers, and this file's own summaries are **not**
   authoritative sources.
2. **Record the verification**: in an ADR or a code comment at the
   integration point, note the official doc URL, the date checked, and the
   API version. A capability claim with no recorded verification is treated
   as unverified.
2a. **Tag every factual claim `[PRIMARY]` or `[SECONDARY]`** — in the plan,
   in ADRs, in commit messages, and in anything said to the user.
   `[PRIMARY]` means verified against platform-official documentation, an
   official changelog, or a court record; everything else is `[SECONDARY]`
   and is provisional. **Never present a secondary-sourced claim as
   verified**, never silently promote one to primary, and when a primary
   source is found, replace the secondary citation rather than stacking
   both. Where only a secondary source exists, say so explicitly and state
   what would settle it.
3. **Re-verify when touching an integration** that has not been checked
   recently. Platform APIs deprecate metrics and change auth paths on
   months-long cycles.
4. **Confirm empirically as well as documentarily.** Log the actual field
   set a live endpoint returns and compare it to the docs; discrepancies go
   in an ADR. Documentation can be stale too.
5. **Always distinguish metadata access from media access.** "The API
   returns the video" is ambiguous and has already caused one planning
   error. State explicitly whether an endpoint returns metadata, metrics,
   a *playable/embeddable link*, or a *downloadable file* — and whether any
   media URL **expires** (Instagram's do; download immediately or lose it).
6. **Instagram: use the Facebook Login path**, not Instagram Login — hashtag
   search and Business Discovery exist only there. Do not "simplify" to
   Instagram Login without re-reading §2.1 of the plan.
7. **TikTok: the Research API is off-limits** — academic/non-profit
   eligibility only, and it prohibits commercial use.

---

## 3. Scientific evidence rules

1. **Never fabricate data, metrics, benchmark numbers, or results.** If a
   number has not been measured on the real hardware and the real corpus,
   label it `[ASSUMPTION]` and say so.
2. **Never present an unverified projection as a fact**, in documentation,
   commit messages, dashboard copy, or conversation. §10's cost table is now
   measured (Phases 0A/0B), **except storage, which stays `[PROVISIONAL]`**
   — 14 files, one niche, a 9.6× spread. A single-sample measurement is not
   a measurement; benchmark a range before projecting from it.
3. **Maintain the three-way distinction everywhere** — code comments, stored
   recommendation records, dashboard output:
   `verified capability` / `engineering assumption` / `open research
   question`.
4. **Correlational findings are never described as causal.** The only
   causal evidence this project can produce comes from the Phase 8
   pre-registered experiment, with its stated power limits.
5. **Never claim to have reconstructed a platform's ranking algorithm**, and
   **never promise or imply guaranteed virality** — in any phrasing,
   including hedged marketing-style phrasing.
6. **All five validation tests are mandatory** before any claim of model
   value: chronological (V1), creator-grouped (V2), unseen-creator (V3),
   **transfer-to-own-account (V4)**, walk-forward drift (V5). Report each
   separately with uncertainty intervals. **Never average V1–V4 into a
   single headline metric** — V4 failing while V1–V3 pass is a specific,
   actionable diagnosis that averaging would hide.
7. **Beat both baselines or say you didn't**: (a) predict the creator's own
   rolling median, (b) generic AI advice produced without ViralLab's
   pipeline. A no-beat result is a valid, publishable phase outcome — report
   it plainly and label any recommendation shown afterwards as
   not-yet-validated.
8. **Predict relative performance**, not raw view counts.
8a. **Compare only at fixed ages.** Every metric used for labeling,
   modeling, or experiment analysis must carry an explicit **anchor age**
   (`views@7d`, `views@30d`, `views@90d`). **Never compare videos whose
   metrics were captured at different ages** — that measures elapsed
   exposure, not content quality. Both sides of a ratio must share the same
   anchor, including the creator baseline in the denominator. Read 7-day
   anchors at **day 9+** because Instagram lags up to 48 hours. Never mutate
   raw snapshots; age anchoring is a read-time derivation. Keep the
   automated anchor-consistency check passing — this is the easiest
   invalidating error in the project to make silently.
9. **Only pre-publication-knowable features may predict** for the
   recommendation model. Post-publication signals are targets or a separate
   model — never both feature and target in one model. Keep the automated
   leakage check passing.
10. **Chronological and grouped splits only.** Never randomly shuffle a time
    series.
11. **Stratified samples carry their weights.** Any training set drawn from
    `analysis_samples` must apply inverse-probability weighting; unweighted
    training on a stratified sample silently misstates base rates.
12. **Preserve representative sampling across all three performance strata.**
    Never let Tier 2/3 analysis drift toward analysing only successful
    videos — without average and underperforming videos, nothing can
    identify what *distinguishes* a hit.
13. **Pre-register experiment predictions before publishing**, never after.
    Honour the pre-registered stopping rule. An inconclusive result stays
    inconclusive; do not reinterpret it as success.

---

## 4. Processing safeguards

Every new job type must satisfy all of these — they are not optional
extras:

1. **Idempotency.** Carry an `idempotency_key`
   (`{job_type}:{video_id}:{method_version}`) under a unique index; write
   results by **upsert** on that key. Running a job twice must produce
   exactly one result row.
2. **Lease ownership validation.** Every heartbeat, result submission, and
   completion call must present the `lease_token` minted at dequeue. A
   worker whose lease was reaped **must not** be able to write results —
   mismatches return 409 and are logged. Never add a write path that skips
   this check.
3. **Heartbeats.** Long jobs extend their lease; the reaper requeues only
   leases that stopped heartbeating. A slow-but-alive job is never killed
   for being slow.
4. **Bounded retries.** Respect `max_attempts` (default 3) with exponential
   backoff via `not_before`, then terminal `failed` with the error recorded
   and dead-lettered to the dashboard. **No job may retry forever.**
5. **Resumability.** A killed worker must never lose or duplicate work.
6. **Secure transport.** LAN-bound and firewalled; **never expose the API to
   the internet.** TLS with the self-signed CA pinned on the worker, plus a
   rotatable per-worker bearer token in `.env`. Media is fetched only via
   HMAC-signed, short-lived, single-video URLs — never a blanket static-file
   mount.
7. **Clean-up after confirmation.** The worker deletes its local media copy
   only once the server confirms the result was persisted.
8. **Idempotent collection.** Re-running any collector or ingestion must not
   duplicate rows.

---

## 5. Benchmark-before-scale discipline

1. **Measure on the real RTX 2070 and the real corpus before any scaling
   decision.** Published benchmarks for other GPUs are starting estimates,
   never substitutes — no RTX 2070 figures exist in the literature for this
   stack.
2. **Benchmark a small representative sample first** — and representative
   means spanning short/long, speech-heavy/music-only, static/high-motion,
   and your own content as well as niche content.
3. **No cost or throughput figure enters documentation as fact until
   measured.** Until then it is tagged and presented as a projection whose
   purpose is to be falsified.
3a. **Benchmark total cost, not inference time.** Every cost measurement
   must account separately for **model cold-load time** (per model, measured
   apart from inference), media **download**, **decode/frame-extraction**,
   per-modality **inference**, **Tier 3** as its own figure rather than an
   extrapolation from Tier 2, **observed storage footprint**, peak VRAM and
   RAM, and **retry/failure overhead**. Reporting inference time alone
   understates real cost and has already been corrected once in this plan.
   If cold-load time turns out to dominate, batch by **modality** rather
   than by video — and settle the job-granularity change in an ADR before
   any schema depends on it.
4. **Re-derive the cost model when measurements contradict projections**,
   before scaling — not after.
5. **Benchmark model *usefulness*, not only speed.** A fast VLM producing
   vague captions is worthless; qualitative review is part of the gate.
6. **Respect the tier budget.** Analysis is progressive by design (Tier 1
   metadata screening on everything → Tier 2 selective multimodal → Tier 3
   deep). Do not promote work to a more expensive tier without a reason
   recorded against the plan's sampling design.
7. **Remember the hardware ceiling (measured, Phase 0B):** Qwen2.5-VL Q4
   peaks at **7634 MiB of 8192** — 93% of the card, ~558 MiB spare against a
   1517 MiB desktop baseline. Whisper and the VLM **cannot** be co-resident.
   One modality in VRAM at a time, always; check free VRAM before leasing a
   vision batch, and never run other GPU work during a batch.
8. **Batch by modality, never per video (measured, Phase 0B).** VLM cold
   load is **44.7 s** against **9.0 s** of inference for a 4-frame video —
   5:1 against useful work. Lease a batch, run one modality across all of
   it, unload, load the next. Per-video idempotency keys are still required.
9. **Never store ASR output unchecked (measured, Phase 0B).** Whisper
   *hallucinates* on music-only audio ("Thank you.", "Music", "Hello, I am
   a" repeated) rather than returning empty, and only 1–2 of 14 niche videos
   had real speech. Gate ASR behind a speech-presence check and store
   **NULL** when none is detected. For this niche, **VLM-read on-screen text
   is the primary text channel**, not speech.
10. **Use Whisper `small`, not `medium`** — 258× vs 102× realtime, lower cold
   load, and the output is near-worthless for music content either way.

---

## 6. Technical standards

- Python-first backend. Prefer simple, proven technology over novel
  technology.
- **Do not overengineer, and do not add services.** The target topology is
  **three long-running processes**: Postgres, the Ubuntu FastAPI app, the
  Windows worker. No Kubernetes, no message broker, no separate vector
  database, no microservice split, no new abstraction layer without a
  compelling written justification approved first. One Postgres serves data,
  queue, and vectors deliberately.
- **Avoid unnecessary dependencies.** Each new package needs a reason beyond
  convenience.
- Modular architecture with clear seams between collection, screening,
  analysis, modeling, recommendation, and experiment. Platform-specific code
  stays behind `DataSourceAdapter`; GPU code stays behind the pipeline
  interfaces.
- Structured logging and real error handling — no bare `print`, no
  `except: pass`.
- Automated tests: collectors against fixtures (never live APIs in CI), the
  job round-trip plus the four adversarial job tests as permanent regression
  tests, and the statistical invariants of §3.
- Versioned datasets and reproducible experiments: every trained model
  records its training window, feature version, and sampling weights.
- Credentials in a gitignored `.env` only; never committed, never logged.
  Revisit secret handling before any multi-user or commercial use.

---

## 7. Cost and legal rules

- **Never silently introduce a paid service.** Any new paid dependency,
  cloud API call, or data provider requires explicit approval first and must
  respect the cap in `config/settings.yaml`.
- **Never silently exceed a configured resource or spending limit.** Overage
  requires explicit confirmation, never an automatic charge.
- **Prefer official APIs** for own-account data — free, supported,
  low-risk.
- For third-party collection, follow the operating policy in
  `DEVELOPMENT_PLAN.md` §2.3: public logged-out data only; no circumvention
  of technical access controls; no fake accounts; aggressive rate limiting;
  internal research use only; no redistributable creator database. Flag for
  legal review before any commercialization involving that data.
- Respect platform terms, rate limits, `robots.txt`, and privacy law (GDPR
  applies). When in doubt about a collection method, raise it rather than
  implementing it.
- Your own TikTok masters come from **your local archive** where one
  exists. **Narrowed by ADR 0007 (2026-10-26):** the archive covers
  only 156 of 234 publications and holds performance masters rather
  than published edits, so own-account *published* video may be
  fetched with `yt-dlp` via `server/collectors/tiktok_archive.py`.
  Ownership is proven by presence in the authenticated owner's
  TikTok `video/list` response, never by configuration. This does
  **not** extend to third-party media, and the code cannot be
  configured to extend to it.

---

## 8. Scope discipline

- Dashboard polish, automated publishing, trend intelligence, and commercial
  features are **not** MVP (Phases 9–11). Do not drift into them.
- The MVP ends with **Phase 8, the live own-account experiment** — the MVP's
  job is to test whether recommendations *improve outcomes*, not merely to
  generate recommendations.
- The primary goal is scientific. An impressive UI that doesn't answer the
  outcome question is a failure, not a milestone.
