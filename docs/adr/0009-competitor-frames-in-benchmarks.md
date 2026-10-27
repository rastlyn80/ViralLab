# ADR 0009 — Competitor frames in a held-out benchmark

**Date:** 2026-10-27
**Status:** Accepted
**Driver:** M3 in `docs/BLUEPRINT.md` requires a held-out label set of
"40 own + 40 competitor openings, labelled from full frame strips before
any inference". M2 made competitor media transient. The two requirements
collide, and the collision is not obvious until you try to build the set.

## The conflict

`docs/MULTIMODAL_PIPELINE.md` §0 states the retention policy as a table:

| | own content | competitor content |
|---|---|---|
| metadata | retained indefinitely | retained indefinitely |
| derived features | retained indefinitely | retained indefinitely |
| **media bytes** | retained | **transient only** |

A frame strip is media bytes. It is a decoded copy of several moments of
someone else's video, and calling it "derived" because ffmpeg touched it
would be a definition chosen for convenience. Under the policy as
written, 40 competitor frame strips cannot be kept — and a held-out
benchmark set that evaporates after six hours is not a benchmark.

M3's own text already anticipates part of this: "Competitor frames go to
a cloud provider only if the provider's terms and the source's permit it
— recorded, not assumed." It did not anticipate that keeping them at all
was the prior question.

## Options considered

1. **Retain a bounded set of competitor frames** under an explicit
   exception — say 40 videos × 6 frames — on the grounds that it is small
   and internal. Rejected: the policy's value is that it has no "small
   enough" clause. The first exception is the one that makes the second
   arguable, and §2.3 already flags this area for legal review before any
   commercialisation.
2. **Build the held-out set from own media only**, 80 own openings.
   Rejected: the reason M3 wants competitor openings is that a model
   which works on one creator's framing, lighting and instrument may not
   generalise, and that is precisely the failure the benchmark exists to
   catch. Removing the out-of-domain half removes the point.
3. **Label inside the retention window; keep the labels, not the
   frames.** Accepted.

## Decision

**Labels are derived data and persist. Frames are media bytes and do
not.**

Concretely, the held-out competitor set is built like this:

- Acquire the 40 competitor publications through the normal M2 path, so
  they are registered in `transient_media` with the usual six-hour
  deadline and the usual audit row.
- Extract frame strips and label them **within that window**. The
  labelling output — video id, field, value, who labelled it, when, and
  the method version of the frame plan — is derived data and is stored
  permanently.
- The frames and the mp4 are released by the normal path, with
  `delete_reason='features_persisted'`, once the labels are stored.
- Scoring a model later means re-acquiring the same publications and
  re-extracting frames deterministically from the same frame plan. The
  labels, which are the expensive part, never need redoing.

## Why this is affordable, which is what makes it tenable

Measured 2026-10-27: competitor acquisition costs **$0.003–0.004 per
file**. Reproducing the entire 40-video competitor benchmark set costs
about **$0.14** and a few minutes. An argument for retention that rests
on "re-fetching would be expensive" does not survive contact with that
number.

The frame plan is deterministic (`server/ai/vision_task.FramePlan`), so
a re-extraction at the same method version yields the same frames. If
that ever stops being true the method version changes, which is already
the signal that a benchmark needs re-scoring.

## Consequences

- The benchmark is reproducible but not instantaneous: re-scoring has a
  dependency on the provider still serving those publications. A
  publication that is deleted by its creator becomes unscoreable, and
  that is recorded as attrition rather than quietly dropped.
- Labelling becomes time-boxed work. Forty openings must be labelled
  before the deadline, or the acquisition is wasted. The window can be
  raised for a labelling session, but only deliberately and only by
  changing `transient.TTL` with the change recorded.
- The labels table must store enough to re-identify the exact frames: the
  video id, the frame plan's method version, and the timestamps labelled.
  A label with no frame plan attached cannot be re-scored against
  anything.
- **This does not grant permission to send competitor frames anywhere.**
  It governs retention only. M3's separate condition still stands: a
  cloud vision provider sees competitor frames only if that provider's
  terms and the source's permit it, recorded per request, and only once
  the `cloud_vision` category cap is non-zero — it is currently $0.00.

## Review trigger

Re-read this before any change that would keep competitor-derived imagery
for longer than one acquisition window, including thumbnails, cover
images and cropped regions. The question is always the same one: is this
a measurement, or is it a copy of someone's video?
