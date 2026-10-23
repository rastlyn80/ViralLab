# ADR 0004 — GPU memory safety checks

**Date:** 2026-10-24
**Status:** Accepted (owner-approved 2026-10-24)
**Driver:** Phase 0B measurement — `docs/benchmarks/0B-local-ai.md` §3

## Context

Measured on the actual card:

| | MiB | Of 8192 |
|---|---|---|
| Desktop idle, before any work | **1517** | 19% |
| faster-whisper `small` int8 | 1896–2038 | 25% |
| **Qwen2.5-VL 7B Q4** | **7634** | **93%** |

**~558 MiB of headroom**, and that is against a *quiet* desktop. The machine
is someone's workstation: a browser with hardware acceleration, a video call,
a game, or another model would push the total past 8192 MiB. The failure modes
are an outright CUDA OOM (recoverable, wasteful) or silent partial offload to
CPU (far worse — the batch still completes but at a fraction of the measured
throughput, quietly invalidating every cost projection).

The plan's `[SECONDARY]` estimate of ~4.5 GiB for INT4 was too optimistic;
the real figure including vision tower and KV cache is 7.6 GiB.

## Decision

Four checks, enforced in the worker, not left to operator discipline.

### 1. Pre-lease free-VRAM gate

Before leasing a batch, query free VRAM and require

```
free_mib >= model_requirement_mib + SAFETY_MARGIN_MIB
```

with per-modality requirements measured in Phase 0B (`vision` 7634,
`transcription` 2038) and `SAFETY_MARGIN_MIB` default **512**. If the check
fails the worker **does not lease** — it logs, reports the condition for
dashboard display, and sleeps a backoff interval. Work stays queued on the
server, which is exactly the designed behaviour for a machine that is
sometimes unavailable. **Deferring is always preferred to thrashing.**

### 2. One modality resident at a time, verified

The worker asserts the previous model is unloaded before loading the next,
and re-checks free VRAM after unload. If memory has not been released
(a known failure mode with abandoned CUDA contexts), the worker **exits
non-zero** rather than continuing into a degraded state; the supervisor
restarts it clean. A fresh process is a reliable way to release VRAM; a
half-released context is not.

### 3. Mid-batch monitoring with graceful bail-out

VRAM is sampled during the batch (the Phase 0B `VramSampler` pattern). If
free memory falls below a critical floor (default 256 MiB), the worker
**stops leasing new videos within the batch**, finishes the current one,
commits results so far — per-video commits make this safe, per ADR 0002 —
and releases the lease for requeue. Committed work is kept; the remainder
returns to the queue.

### 4. Record actual peak VRAM per batch

Every batch result carries its observed peak. This gives drift detection:
if peaks creep toward the ceiling as prompts, frame counts or models change,
it is visible in data rather than discovered via an OOM. A measured peak that
contradicts the configured requirement is a signal to re-run Phase 0B's
benchmark, not to raise the margin and hope.

## Configuration

In `config/settings.yaml`, not hardcoded:

```yaml
gpu:
  total_vram_mib: 8192          # discovered at startup; config is the expectation
  safety_margin_mib: 512
  critical_floor_mib: 256
  requirements_mib:             # measured, Phase 0B
    vision: 7634
    transcription: 2038
  on_insufficient: defer        # defer | fail  (never: proceed)
  backoff_seconds: 300
```

`on_insufficient: proceed` is deliberately not an option.

## Consequences

**Good**

- Silent CPU offload — the most dangerous outcome, because it corrupts
  throughput measurements without erroring — is prevented structurally.
- The owner can use the workstation normally; batches defer instead of
  fighting for the card or crashing.
- Peak-VRAM telemetry turns a latent risk into an observable trend.

**Bad / accepted**

- Throughput becomes dependent on desktop activity. A busy machine may make
  little progress. This is correct behaviour, and the dashboard must show
  *why* the queue is not draining ("deferred: insufficient VRAM") so it is
  never mistaken for a stall or a bug.
- `nvidia-smi` polling costs a subprocess every 250 ms during a batch —
  negligible against 2.25 s/frame inference, and it was the mechanism that
  produced Phase 0B's numbers, so it is already validated.
- The margins are heuristics calibrated on one machine with one model. They
  are configuration, and they must be re-derived if either changes.

## Verification

Phase 1 acceptance must demonstrate:

1. With VRAM artificially constrained below the vision requirement, the
   worker **defers** rather than leasing or crashing, and the reason is
   visible.
2. Peak VRAM is recorded on a completed batch and matches the Phase 0B
   figure within tolerance.
3. A mid-batch drop below the critical floor commits completed videos and
   releases the lease, losing nothing.
