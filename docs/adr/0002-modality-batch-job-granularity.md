# ADR 0002 — Job granularity: modality batches, per-video idempotency

**Date:** 2026-10-24
**Status:** Accepted (owner-approved 2026-10-24)
**Supersedes:** the implicit per-video job design in `DEVELOPMENT_PLAN.md` §4.2
**Driver:** Phase 0B measurement — `docs/benchmarks/0B-local-ai.md` §2

## Context

Phase 0B measured, on the real RTX 2070 SUPER:

| | Measured |
|---|---|
| Qwen2.5-VL 7B Q4 cold load | **44.7 s** |
| Inference, 4 frames (one Tier 2 video) | **9.0 s** |
| faster-whisper `small` cold load | 1.0–1.2 s |
| VLM peak VRAM | **7634 MiB of 8192** (~558 MiB spare) |

A per-video job would pay the 44.7 s load to do 9.0 s of work — **5:1 against
useful work**, making Tier 2 ≈56 s/video instead of ≈11.4 s. The two models
also cannot be co-resident (7.6 GiB + 2.0 GiB > 8 GiB), so a single job
cannot hold both and must unload between modalities regardless.

## Decision

**The lease unit is a `batch`; the result unit stays per-video.**

1. A **batch** groups work of **one modality** across many videos
   (`vision`, `transcription`, `audio`, `shots`). A worker leases the batch,
   loads that model once, processes every video in it, unloads, and only
   then leases the next batch — which may be a different modality.
2. **Per-video idempotency is preserved.** Every (video, stage,
   method_version) still produces exactly one result row, written by upsert
   on `idempotency_key = {stage}:{video_id}:{method_version}` under a unique
   index. Batching changes *when* work is leased, never what is recorded.
3. **Per-video results are committed incrementally**, not at batch end. A
   batch interrupted after 60 of 100 videos leaves 60 durable results; the
   requeued batch skips them via the idempotency key. No work is repeated
   and none is lost.
4. **Batch size is configurable** (`batch_size`, default 100 for vision)
   and bounded, so a batch cannot grow long enough to outlive a reasonable
   lease.

## Lease, heartbeat and recovery

Unchanged in principle from §4.2, applied at batch level:

- Dequeue mints a random `lease_token`; **every** heartbeat, per-video result
  submission and completion call must present it. A token mismatch returns
  409 and is logged — this is what stops a reaped zombie worker from
  overwriting fresher results.
- The worker heartbeats while the batch runs, extending `lease_expires_at`.
  The reaper requeues only batches that **stopped heartbeating**, so a slow
  batch is never killed for being slow.
- `attempt_count` / `max_attempts` (default 3) with exponential backoff via
  `not_before`, then terminal `failed` and dead-lettered to the dashboard.
  A poison batch cannot loop forever.
- **Resumption is partial-credit by construction:** because results commit
  per video, a retried batch costs only the unfinished remainder plus one
  model load.

## Consequences

**Good**

- Cold load amortises to **~0.46 s/video over a 100-video batch**, a ~5×
  reduction in Tier 2 cost. This is the single largest efficiency decision
  in the pipeline.
- Honours the hard VRAM constraint by design rather than by discipline:
  one modality is resident at a time, always.
- Interruption-safe without transactional batch semantics.

**Bad / accepted**

- A batch is a coarser failure unit: one pathological video can fail a batch
  repeatedly. Mitigated by recording per-video failures inside the batch and
  **quarantining** a video after its own repeated failures rather than
  failing the whole batch again.
- Latency for any single video rises — it waits for its batch. Acceptable:
  nothing here is interactive, and the GPU machine is intermittently on
  anyway.
- Scheduling is slightly more complex: batches must be composed by modality
  **and** tier before leasing.

## Alternatives rejected

- **Per-video jobs with a resident model** (keep the VLM loaded between
  jobs). Rejected: 7634 MiB resident indefinitely on a desktop machine makes
  the box unusable for anything GPU-accelerated and risks OOM when the
  desktop baseline rises. Batching gets the same amortisation with bounded
  residency.
- **Per-video jobs, accept the load cost.** Rejected on measurement: 5× the
  GPU wall-clock for identical output.
- **Transactional batches** (all-or-nothing). Rejected: an interruption at
  video 99 of 100 would discard 99 completed analyses.

## Verification

Phase 1 acceptance must demonstrate, with real output:

1. A batch killed mid-run is requeued by the reaper and completes, with
   **no duplicate result rows** and no re-processing of committed videos.
2. A resurrected worker presenting a **stale `lease_token`** is rejected 409
   and cannot overwrite newer results.
3. The same batch replayed produces **exactly one** result row per video.
4. A deliberately failing batch stops at `max_attempts` and dead-letters.
