"""Handover point from discovery to the GPU worker.

A discovered publication has metadata and performance figures but no
features. This module is the seam where it becomes analysable, and it exists
now - before Phase 6 builds the extractors - so that Phase 4's data model
does not have to be redesigned when they arrive.

It deliberately does **not** run anything on a schedule. What it does:

  * decide which publications are worth analysing, with a **stratified**
    sampler that includes under-performing content on purpose;
  * state what each stage needs and what it would cost;
  * enqueue a batch onto the existing job queue, which already provides
    leases, heartbeats, bounded retries and reaping;
  * refuse, loudly, where media-retention policy or GPU memory gating would
    be violated.

See docs/MULTIMODAL_PIPELINE.md for the full design, including the rule that
governs all of this: **public metadata access is not permission to retain
competitor media.** Features are kept; the bytes are transient.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.db.models import (AnalysisBatch, BatchVideo, Creator,
                              Video)
from server.research import baseline as bl
from server.research.service import observations_for

logger = logging.getLogger("virallab.research.analysis")

#: Media for a competitor publication may exist on disk only this long.
#: Enforced by the job rather than left to habit, because "we meant to
#: delete it" is how a research cache becomes a creator database.
COMPETITOR_MEDIA_TTL = timedelta(hours=6)

#: Performance strata. All three are sampled deliberately: a corpus of hits
#: cannot identify what distinguishes a hit (CLAUDE.md 3.12).
STRATA = ("over", "typical", "under")
#: Relative-performance boundaries against the creator's own local baseline.
OVER_AT = 2.0
UNDER_AT = 0.5


class AnalysisRefused(RuntimeError):
    """Analysis was not enqueued, and why. Not a transient error."""


@dataclass
class AnalysisCandidate:
    video_id: int
    external_id: str
    platform: str
    stratum: str
    relative_performance: float | None
    #: Sampling probability, carried so training can apply inverse-
    #: probability weights. Unweighted training on a stratified sample
    #: silently misstates base rates (CLAUDE.md 3.11).
    sampling_probability: float
    #: Whether a provider media URL is known to exist. Not whether we may
    #: keep it - we may not.
    media_url_known: bool
    is_fixture: bool


@dataclass
class AnalysisPlan:
    creator_id: int
    tier: str
    candidates: list[AnalysisCandidate] = field(default_factory=list)
    by_stratum: dict[str, int] = field(default_factory=dict)
    #: Measured per-stage costs, so a decision to run is taken with numbers.
    estimated_gpu_seconds: float = 0.0
    estimated_transient_mb: float = 0.0
    refusals: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def runnable(self) -> bool:
        return bool(self.candidates) and not self.refusals


#: `[PRIMARY, measured 0B]` on the RTX 2070 SUPER.
VLM_COLD_LOAD_S = 44.7
VLM_PER_FRAME_S = 2.25
WHISPER_REALTIME_FACTOR = 258.0
#: `[PRIMARY, measured 0A]` 0.096 MB per second of video.
MB_PER_SECOND = 0.096

TIER_FRAMES = {"tier2": 4, "tier3": 12}
#: Fraction of a creator's publications analysed per tier, from the
#: progressive design in DEVELOPMENT_PLAN.md 5.2.
TIER_FRACTION = {"tier2": 0.10, "tier3": 0.01}


def plan_for_creator(session: Session, creator_id: int, *,
                     tier: str = "tier2",
                     max_items: int | None = None) -> AnalysisPlan:
    """Which of a creator's publications to analyse, and what it would cost.

    Stratified rather than top-N on purpose. Analysing only the hits would
    produce a feature set that describes successful videos without being
    able to say what *distinguishes* them, which is the whole question.
    """
    if tier not in TIER_FRAMES:
        raise AnalysisRefused(f"unknown tier {tier!r}")

    creator = session.get(Creator, creator_id)
    plan = AnalysisPlan(creator_id=creator_id, tier=tier)
    if creator is None:
        plan.refusals.append("no such creator")
        return plan

    observations = observations_for(session, creator_id)
    points = bl.rolling_baseline(observations)
    videos = {v.id: v for v in session.scalars(
        select(Video).where(Video.creator_id == creator_id)).all()}

    buckets: dict[str, list[AnalysisCandidate]] = {s: [] for s in STRATA}
    for p in points:
        vid = p.observation.video_id
        video = videos.get(vid)
        if video is None:
            continue
        rel = p.relative_performance
        if rel is None:
            # No baseline yet. Excluded rather than guessed into a stratum.
            continue
        stratum = ("over" if rel >= OVER_AT
                   else "under" if rel <= UNDER_AT else "typical")
        buckets[stratum].append(AnalysisCandidate(
            video_id=vid, external_id=video.external_id,
            platform=video.platform, stratum=stratum,
            relative_performance=round(rel, 3),
            sampling_probability=1.0,       # filled in below
            media_url_known=bool((video.provenance or {}).get(
                "capabilities", {}).get("media_url")),
            is_fixture=bool(video.is_fixture)))

    total_scored = sum(len(v) for v in buckets.values())
    if not total_scored:
        plan.refusals.append(
            "no publication has a baseline yet, so none can be placed in a "
            "performance stratum. Collect more history first.")
        return plan

    target = max_items or max(1, round(total_scored * TIER_FRACTION[tier]))
    # Even split across strata, so the under-performers are not crowded out
    # by whichever bucket happens to be largest.
    per_stratum = max(1, target // len(STRATA))

    chosen: list[AnalysisCandidate] = []
    for stratum in STRATA:
        pool = buckets[stratum]
        if not pool:
            plan.notes.append(
                f"no publication falls in the '{stratum}' stratum, so this "
                f"sample cannot speak to that end of the range.")
            continue
        # Highest |deviation| first within a stratum: the clearest examples.
        pool.sort(key=lambda c: abs((c.relative_performance or 1.0) - 1.0),
                  reverse=True)
        take = pool[:per_stratum]
        prob = len(take) / len(pool)
        for c in take:
            c.sampling_probability = round(prob, 4)
        chosen.extend(take)

    plan.candidates = chosen
    plan.by_stratum = {s: sum(1 for c in chosen if c.stratum == s)
                       for s in STRATA}

    frames = TIER_FRAMES[tier]
    durations = [videos[c.video_id].duration_s or 30.0 for c in chosen]
    plan.estimated_gpu_seconds = round(
        VLM_COLD_LOAD_S + len(chosen) * frames * VLM_PER_FRAME_S
        + sum(durations) / WHISPER_REALTIME_FACTOR, 1)
    plan.estimated_transient_mb = round(sum(durations) * MB_PER_SECOND, 1)

    plan.notes.append(
        f"Sampling probabilities are recorded per stratum. Training on this "
        f"sample MUST apply inverse-probability weights, or it will "
        f"misstate how common over-performance is.")
    if any(c.is_fixture for c in chosen):
        plan.notes.append(
            "Some candidates are fixture data. Features extracted from "
            "fabricated media would be meaningless, so a real run must "
            "exclude them.")
        plan.refusals.append(
            "this plan contains fixture publications; analysis of synthetic "
            "media would produce fabricated features")
    if not creator.is_own_account:
        plan.notes.append(
            f"Competitor media is transient: fetched, analysed and deleted "
            f"within {COMPETITOR_MEDIA_TTL}. Features are retained, the "
            f"bytes are not, and nothing is served through the API. Public "
            f"metadata access is not permission to retain media.")
    return plan


def enqueue(session: Session, plan: AnalysisPlan, *,
            method_version: str = "features-v1") -> int:
    """Put a plan onto the existing job queue.

    Not implemented, deliberately. The queue, leases, heartbeats, retries
    and reaper all exist and are tested; the extractors do not. Raising here
    rather than silently enqueueing work nothing can run is the honest
    behaviour, and it is what makes this module a seam rather than a stub
    that looks finished.
    """
    if not plan.runnable:
        raise AnalysisRefused(
            "plan is not runnable: " + "; ".join(plan.refusals or
                                                 ["no candidates"]))
    raise AnalysisRefused(
        "feature extraction is not implemented (Phase 6). This plan is "
        f"ready: {len(plan.candidates)} publication(s), "
        f"{plan.by_stratum}, about {plan.estimated_gpu_seconds:.0f}s of GPU "
        f"time and {plan.estimated_transient_mb:.0f} MB of transient media. "
        f"See docs/MULTIMODAL_PIPELINE.md.")


# ---------------------------------------------------------------------------
# the feature contract
#
# What a future analysis pass will produce, declared NOW so that the storage
# written in Phase 4.5 does not have to be redesigned when the extractors
# arrive, and so that a caller can see what it is asking for before any GPU
# time is spent.
#
# Every field is `None` until an extractor fills it. There is no default and
# no placeholder: an absent feature must stay distinguishable from a feature
# that was measured and came back empty, which is the same rule that governs
# provider capabilities.
#
# `confidence` and `needs_human_review` are mandatory on every produced
# item, because an AI classification is not ground truth (CLAUDE.md 3.3 and
# docs/MULTIMODAL_PIPELINE.md 3). A label with no confidence and no review
# path is indistinguishable from a fact, and will eventually be treated as
# one.
# ---------------------------------------------------------------------------

FEATURE_CONTRACT_VERSION = "features-v1"

#: modality -> what it will produce. Keys are stable; adding a field is a
#: version bump, not an edit.
FEATURE_CONTRACT: dict[str, dict] = {
    "audio": {
        "requires": "audio track",
        "model": "[OPEN] not selected; Whisper small for speech only",
        "fields": {
            "musical_work": "str | None - the piece, where identifiable",
            "composer": "str | None",
            "work_identification_method": (
                "audio_match | metadata_mention | human - never a guess "
                "presented as a match"),
            "passage": "str | None - which section of the work",
            "tempo_bpm": "float | None",
            "key_signature": "str | None",
            "dynamic_range_db": "float | None",
            "rubato_degree": "float | None - timing freedom, 0..1",
            "articulation": "str | None - legato / staccato / mixed",
            "performance_character": (
                "str | None - the interpretive quality, which is a "
                "judgement and is labelled as one"),
            "speech_present": "bool - gates ASR entirely",
            "spoken_words": (
                "str | None - NULL when no speech is detected. Whisper "
                "hallucinates on music-only audio (measured, Phase 0B), so "
                "this is never stored unchecked"),
        },
    },
    "vision": {
        "requires": "video frames",
        "model": "[OPEN] Qwen2.5-VL Q4 measured at 7634/8192 MiB VRAM",
        "fields": {
            "on_screen_text": (
                "list[str] - the PRIMARY text channel for this niche, not "
                "speech (measured, Phase 0B)"),
            "hook_description": "str | None - what the first 3 seconds do",
            "hook_frames_analysed": "int",
            "camera_angle": "str | None - keyboard / face / wide / overhead",
            "framing": "str | None - close / medium / wide",
            "camera_movement": "str | None - static / handheld / pan",
            "lighting": "str | None - natural / warm / dramatic / flat",
            "setting": "str | None - concert hall / home / studio / outdoor",
            "instrument_visible": "bool | None",
            "performer_visible": "bool | None",
            "cut_count": "int | None",
            "mean_shot_seconds": "float | None",
            "transition_kinds": "list[str]",
            "text_overlay_present": "bool | None",
            "emotional_presentation": (
                "str | None - a judgement about presentation, never about "
                "the performer"),
            "narrative_structure": "str | None - demo / story / reveal",
        },
    },
    "cross_platform": {
        "requires": "the same performance published on both platforms",
        "model": "no model; derived from existing publication links",
        "fields": {
            "paired_video_id": "int | None",
            "duration_delta_s": "float | None",
            "caption_differences": "dict | None",
            "edit_differences": "dict | None",
            "platform_specific_choices": (
                "list[str] - what the creator changed for each platform, "
                "which is only answerable where a confirmed pair exists"),
        },
    },
}

#: Attached to every produced item, without exception.
REQUIRED_ITEM_FIELDS = (
    "confidence",            # the model's own, uncalibrated
    "confidence_calibrated",  # False until demonstrated on this corpus
    "needs_human_review",    # True for every interpretive field
    "method_version",
    "produced_at",
)


class AnalysisRequestRefused(AnalysisRefused):
    """A specific publication could not be queued, and why."""


@dataclass
class AnalysisRequest:
    """A queued request to analyse one publication."""

    video_id: int
    external_id: str
    platform: str
    tier: str
    modalities: list[str]
    batch_ids: list[int] = field(default_factory=list)
    #: What will be fetched, and for how long it may exist on disk.
    media_policy: str = ""
    estimated_gpu_seconds: float = 0.0
    estimated_transient_mb: float = 0.0
    will_run: bool = False
    why_not: str | None = None
    notes: list[str] = field(default_factory=list)


def request_analysis(session: Session, video_id: int, *,
                     tier: str = "tier2",
                     modalities: tuple[str, ...] = ("vision", "audio"),
                     requested_by: str = "ui") -> AnalysisRequest:
    """Queue analysis of ONE publication.

    This is the contract the interface calls. It creates real queue rows -
    the queue already provides leases, heartbeats, bounded retries and
    reaping - so the request is durable and visible. It does **not** run
    anything: no extractor exists yet, so the batch sits `pending` until
    Phase 6 provides a worker that understands these modalities.

    That is deliberate and is reported in `will_run`. A request that looked
    accepted and then silently never ran would be worse than a refusal.
    """
    video = session.get(Video, video_id)
    if video is None:
        raise AnalysisRequestRefused(f"no such publication {video_id}")
    if tier not in TIER_FRAMES:
        raise AnalysisRequestRefused(f"unknown tier {tier!r}")
    unknown = [m for m in modalities if m not in FEATURE_CONTRACT]
    if unknown:
        raise AnalysisRequestRefused(
            f"unknown modality {unknown}; the contract covers "
            f"{sorted(FEATURE_CONTRACT)}")

    creator = session.get(Creator, video.creator_id)
    own = bool(creator and creator.is_own_account)

    req = AnalysisRequest(
        video_id=video.id, external_id=video.external_id,
        platform=video.platform, tier=tier, modalities=list(modalities))

    if video.is_fixture:
        raise AnalysisRequestRefused(
            "this is a fixture publication. Extracting features from "
            "fabricated media would produce fabricated features, which is "
            "worse than having none.")

    media_url_known = bool(
        ((video.provenance or {}).get("capabilities") or {}).get("media_url"))
    if own:
        req.media_policy = (
            "Own content: the local master or preserved copy is used, and "
            "the worker deletes its copy once the server confirms the "
            "result was persisted (CLAUDE.md 4.7).")
    else:
        if not media_url_known and video.media_status != "available":
            raise AnalysisRequestRefused(
                "no media is reachable for this publication. The provider "
                "did not supply a media URL, and nothing is stored - "
                "correctly, because competitor media is not retained.")
        req.media_policy = (
            f"Third-party content: media is fetched at analysis time, held "
            f"for at most {COMPETITOR_MEDIA_TTL}, and deleted once features "
            f"are persisted. The bytes are never added to the archive and "
            f"never served through the API. Having a provider URL is not "
            f"permission to keep the file.")
        req.notes.append(
            "`[OPEN]` Whether transient download is appropriate for every "
            "source is a question for legal review before "
            "commercialisation (DEVELOPMENT_PLAN.md 2.3).")

    frames = TIER_FRAMES[tier]
    duration = video.duration_s or 30.0
    gpu = 0.0
    if "vision" in modalities:
        gpu += VLM_COLD_LOAD_S + frames * VLM_PER_FRAME_S
    if "audio" in modalities:
        gpu += duration / WHISPER_REALTIME_FACTOR
    req.estimated_gpu_seconds = round(gpu, 1)
    req.estimated_transient_mb = round(duration * MB_PER_SECOND, 2)

    # One batch per modality. Batching by modality rather than by video is
    # not a preference: VLM cold load is 44.7 s against 9.0 s of inference
    # for a 4-frame video (measured, Phase 0B), so per-video batches spend
    # five times more on loading than on work.
    for modality in modalities:
        batch = AnalysisBatch(
            modality=modality, tier=tier,
            method_version=FEATURE_CONTRACT_VERSION, status="pending")
        session.add(batch)
        session.flush()
        session.add(BatchVideo(batch_id=batch.id, video_id=video.id,
                               frames=frames, state="pending"))
        req.batch_ids.append(batch.id)
    session.flush()

    req.will_run = False
    req.why_not = (
        "Queued, but nothing will execute it yet: no worker implements "
        "these modalities. Phase 6 provides the extractors. The request is "
        "durable and will be picked up when one exists.")
    req.notes.append(
        f"Idempotency key will be "
        f"{{modality}}:{video.id}:{FEATURE_CONTRACT_VERSION}, under a "
        f"unique index, so running the job twice produces exactly one "
        f"result row (CLAUDE.md 4.1).")
    req.notes.append(
        "Every produced item must carry "
        f"{', '.join(REQUIRED_ITEM_FIELDS)}. An interpretive label with no "
        f"confidence and no review path becomes treated as a fact.")
    logger.info("queued analysis request for video %s (%s), tier %s, "
                "batches %s, requested_by=%s", video.id, video.external_id,
                tier, req.batch_ids, requested_by)
    return req


def pending_requests(session: Session, *, limit: int = 50) -> list[dict]:
    """Queued analysis that has nothing to run it yet.

    Surfaced so a growing queue of never-executed work is visible rather
    than quietly accumulating.
    """
    rows = session.execute(
        select(AnalysisBatch, BatchVideo, Video)
        .join(BatchVideo, BatchVideo.batch_id == AnalysisBatch.id)
        .join(Video, Video.id == BatchVideo.video_id)
        .where(AnalysisBatch.status == "pending")
        .order_by(AnalysisBatch.id.desc()).limit(limit)).all()
    return [{
        "batch_id": b.id, "modality": b.modality, "tier": b.tier,
        "method_version": b.method_version, "state": bv.state,
        "video_id": v.id, "external_id": v.external_id,
        "platform": v.platform,
        "runnable": False,
        "why_not": ("no worker implements this modality yet (Phase 6)"),
    } for b, bv, v in rows]
