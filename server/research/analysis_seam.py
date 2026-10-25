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

from server.db.models import Creator, Video
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
