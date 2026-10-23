# ViralLab

A local-first system for short-form video performance analysis and
evidence-based content recommendations, built initially for one creator's
TikTok and Instagram Reels accounts.

**Status: pre-MVP. Phase 0A (data-access feasibility) in progress.**
No application code exists yet — only feasibility probes. Infrastructure
work does not begin until the Phase 0A and 0B gates pass.

## Read these first

| Document | What it is |
|---|---|
| [`DEVELOPMENT_PLAN.md`](DEVELOPMENT_PLAN.md) | The plan of record: architecture, phases, gates, methodology, costs, open questions. |
| [`CLAUDE.md`](CLAUDE.md) | Permanent development rules — API verification, scientific evidence, processing safeguards, benchmark-before-scale. |
| [`docs/benchmarks/0A-data-access.md`](docs/benchmarks/0A-data-access.md) | Live Phase 0A report. |
| [`docs/adr/`](docs/adr/) | Architecture and verification decision records. |

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

## Architecture in one line

Three long-running processes: PostgreSQL (data + job queue + pgvector), a
FastAPI app on an always-on Ubuntu server, and a GPU worker on an
occasionally-on Windows machine that pulls jobs when it is awake.

## Repository layout

```
probes/      Phase 0A/0B feasibility probes (throwaway, stdlib only)
docs/        plan artefacts: ADRs, benchmark reports
server/      (not yet created) Ubuntu services
gpu_worker/  (not yet created) Windows GPU worker
shared/      (not yet created) schemas shared by both
```

## Running the Phase 0A probe

```bash
cd probes/data_access
python score_provider.py fixture_good.json   # self-test, expect exit 0
python score_provider.py fixture_bad.json    # negative control, expect exit 1
```

See [`probes/data_access/README.md`](probes/data_access/README.md).

## Data handling

Raw provider samples and media are **gitignored on purpose**. Committing
other creators' data would redistribute personal data and breach the
operating policy in `DEVELOPMENT_PLAN.md` §2.3. Only synthetic fixtures are
tracked.

This repository is private.
