# ViralLab

A local-first system for short-form video performance analysis and
evidence-based content recommendations, built initially for one creator's
TikTok and Instagram Reels accounts.

**Status: Phase 1 complete. Phase 2 TikTok collection is LIVE** — 234 videos
backfilled from the owner's account via the Display API. Instagram app
registered but not yet authorized. Last verified 2026-10-24.

| | |
|---|---|
| Automated tests | **79 passing** — on Windows (PG 17 / py3.12) *and* Ubuntu (PG 18 / py3.14) |
| Cross-machine acceptance | **11/11** — Windows worker → Ubuntu server over TLS |
| Infrastructure | live on both machines |

## Read these first

| Document | What it is |
|---|---|
| [`DEVELOPMENT_PLAN.md`](DEVELOPMENT_PLAN.md) | The plan of record: architecture, phases, gates, methodology, costs, open questions. |
| [`CLAUDE.md`](CLAUDE.md) | Permanent development rules — API verification, scientific evidence, processing safeguards, benchmark-before-scale. |
| [`docs/adr/`](docs/adr/) | Architecture and verification decision records (0001–0005). |
| [`docs/benchmarks/`](docs/benchmarks/) | Phase 0A/0B measurement reports and the provider ToS review. |

## What it does (planned)

Collects performance data for the owner's own accounts via official APIs,
samples comparable videos in the same niche, extracts multimodal features
from the videos themselves, models **relative** performance against each
creator's own baseline at a **fixed video age**, and generates
evidence-tagged recommendations — then runs a pre-registered experiment to
test whether those recommendations actually improve outcomes.

It does **not** attempt to reconstruct platform ranking algorithms and does
not promise virality. Findings are correlational unless the live experiment
supports otherwise, and the distinction is enforced throughout.

## Phase status

| Phase | State |
|---|---|
| **0A** Data-access feasibility | ✅ passed — media *is* downloadable, cost $0.30–3.00/1k measured, ToS reviewed |
| **0B** Local AI benchmark | ✅ passed — Whisper 258× RT, VLM 2.25 s/frame, 7634 MiB peak |
| **1** Infrastructure scaffold | ✅ **complete** — schema, queue, API, worker, real two-machine round trip |
| **2** Own-account collection | 🟡 **TikTok live on the authoritative server** — 234 videos migrated to Ubuntu with original ids and timestamps preserved, verified field by field; daily collector running and restart-safe. **Not** complete: 7-day unattended run not yet elapsed; Instagram not authorized; media-ingest watcher not built |
| 3–8 | not started (MVP ends at Phase 8, the live experiment) |

## Architecture

Three long-running processes, deliberately:

| Where | What |
|---|---|
| **Ubuntu** (`gabry-ubuntu`, always on) | **Authoritative database.** PostgreSQL 18 + pgvector (data, job queue, vectors), FastAPI on `:8443` over TLS, in-process reaper **and daily collector** |
| **Windows** (`Gabriele-Studio`, RTX 2070 SUPER, intermittent) | GPU worker only — polls, leases, analyses, submits. Collection is disabled here (`VIRALLAB_COLLECTION_ENABLED=0`) so the dataset cannot fork |

The worker always initiates. When the Windows machine is off nothing blocks:
batches accumulate as rows. Communication is over Tailscale; the API binds to
the **tailnet address only**, never `0.0.0.0`.

Key design decisions, each driven by a measurement:

- **Batch by modality, not per video** (ADR 0002) — the VLM's 44.7 s cold load
  against 9.0 s of inference is 5:1 against useful work.
- **Python 3.12 on the worker, 3.14 on the server** (ADR 0003) — the ML wheels
  do not exist for 3.14; the server has no ML dependencies.
- **GPU memory gating** (ADR 0004) — the VLM peaks at 7634 of 8192 MiB, so the
  worker defers rather than risking silent CPU offload.
- **Rotation-aware OAuth storage** (ADR 0005) — TikTok rotates its refresh
  token on every refresh, so both tokens are written together under a row
  lock. Instagram's Page token, by contrast, does not expire.
- **Fixed-age metrics are never faked** (`server/analytics/fixed_age.py`) —
  a backfilled video's first snapshot is a lifetime total at an arbitrary
  age (median 237 days here), so `views@7d` returns `None` with a reason
  rather than the lifetime figure. No extrapolation, no "closest available".

## Repository layout

```
server/      Ubuntu: api/, db/ (+ migrations), queue/, collectors/
gpu_worker/  Windows: worker.py, vram.py
shared/      schemas/ and utils/ imported by BOTH halves
scripts/     remote.py, deploy.py, acceptance_phase1.py, provision/
probes/      Phase 0A/0B feasibility probes (throwaway, stdlib only)
docs/        adr/, benchmarks/
tests/       server/, gpu_worker/, shared/
```

## Running the tests



The suite TRUNCATES tables, so it refuses to run against anything but an
explicitly disposable database (`tests/conftest.py`):

```bash
# must end in _test, and must differ from VIRALLAB_DB_URL
export VIRALLAB_TEST_DB_URL="postgresql+psycopg://virallab:...@127.0.0.1:5433/virallab_test"
.venv312/Scripts/python -m pytest tests/        # 79 tests

python scripts/acceptance_phase1.py             # 11 cross-machine checks
python scripts/deploy.py --test                 # sync to Ubuntu + run there
```

Point `VIRALLAB_TEST_DB_URL` at the application database and the run aborts
before touching a single table.

`scripts/provision/verify_reaper.sh` proves the background reaper recovers a
crashed worker's batch without any manual trigger (~40 s).

## Known minor issues

Recorded rather than fixed, so scope stays where the plan puts it:

- `GET /status/queue` needs no auth. Read-only, tailnet-only; it leaks queue
  depth to anything already on the tailnet.
- `config/settings.yaml` carries `spending.monthly_cap_usd` but nothing reads
  it yet — correct for now, since nothing spends money. It must be wired in
  before any paid provider is enabled.
- The reaper logs through the `virallab.api` logger, which uvicorn does not
  configure, so its lines do not appear in `logs/api.log`. The loop itself is
  verified to run.
- Local development uses portable PostgreSQL 17 under `.pgsql/` because the
  winget installer needs UAC elevation. pgvector is unavailable in those
  Windows binaries — fine until Phase 5, and Ubuntu has it from apt.

## Data handling

Raw provider samples, media and anything derived from scraped output
(`*.local.json`) are **gitignored on purpose**: committing other creators'
data would redistribute personal data and breach the operating policy in
`DEVELOPMENT_PLAN.md` §2.3. Only synthetic fixtures are tracked.

The server's TLS **certificate** is committed because the worker pins it; the
private key never leaves the Ubuntu machine. All secrets live in a gitignored
`.env`.

This repository is private.
