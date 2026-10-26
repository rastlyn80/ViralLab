"""Which provider serves a request, and whether it is allowed to cost money.

Four modes, and the difference between them is a policy decision the owner
makes, never an inference this module draws from circumstances:

  `local_only`   nothing leaves the machine. A local failure is a failure.
  `cloud_only`   an authorised cloud provider, or a refusal.
  `hybrid`       local first; escalate only where escalation is separately
                 authorised AND the local result is genuinely weak.
  `compare`      several authorised providers on a bounded sample, for
                 evaluation rather than production.

**The rule this file exists to enforce.** A local model that is missing,
broken or out of memory must never cause a paid request. That is the exact
shape of an accident that spends money: the cheap path breaks at 3am and
the expensive path quietly takes over. `hybrid` escalates on *measured
uncertainty in a successful local result*, never on local failure - and
`escalation_authorised` must be true independently of the mode.

Routing also refuses rather than degrading. If the only provider that can
read video is unauthorised, the answer is "this cannot be done under the
current policy", not "here is a worse answer from frames".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

from server.ai.base import (AIProvider, AnalysisRequest, Hosting, Modality,
                            Registry)

logger = logging.getLogger("virallab.ai.routing")


class Mode(str, Enum):
    LOCAL_ONLY = "local_only"
    CLOUD_ONLY = "cloud_only"
    HYBRID = "hybrid"
    COMPARE = "compare"


#: Default. Chosen so that a fresh install cannot spend anything, and so
#: that competitor media - which is transient and third-party - never
#: reaches a cloud provider by default.
DEFAULT_MODE = Mode.LOCAL_ONLY


@dataclass
class Policy:
    """What is permitted, stated rather than inferred."""

    mode: Mode = DEFAULT_MODE
    #: Must be true for ANY paid call, in every mode. Separate from the
    #: mode so that selecting `hybrid` is not itself an authorisation.
    escalation_authorised: bool = False
    #: Providers the owner has authorised by name. An unlisted provider is
    #: not a candidate even in cloud_only.
    authorised_providers: tuple[str, ...] = ()
    #: Hard ceiling for one request, independent of the monthly cap.
    max_usd_per_request: float = 0.05
    #: Escalate only when the local result's own confidence is below this.
    escalate_below_confidence: float = 0.60
    #: Never send media the owner does not own to a cloud provider.
    allow_third_party_media_to_cloud: bool = False
    #: Bound on a `compare` run, so an evaluation cannot become a spend.
    compare_max_videos: int = 6


@dataclass
class RoutingDecision:
    mode: Mode
    #: Providers to call, in order. Empty with a reason is a valid outcome.
    providers: list[str] = field(default_factory=list)
    estimated_usd: float = 0.0
    requires_authorisation: bool = False
    refused: bool = False
    reason: str = ""
    #: Every provider considered and why it was or was not chosen. The
    #: interface shows this; a routing choice nobody can explain is a
    #: routing choice nobody can trust.
    considered: list[dict] = field(default_factory=list)


def route(registry: Registry, request: AnalysisRequest, policy: Policy, *,
          is_own_media: bool = True,
          local_confidence: float | None = None) -> RoutingDecision:
    """Choose providers for one request under one policy."""
    decision = RoutingDecision(mode=policy.mode)

    candidates = registry.candidates(request)
    if not candidates:
        decision.refused = True
        decision.reason = (
            f"No registered provider accepts "
            f"{[m.value for m in request.required_modalities]}. This is a "
            f"capability gap, not a configuration problem.")
        return decision

    for provider in candidates:
        caps = provider.capabilities()
        ok, why = provider.available()
        entry = {
            "provider": provider.name,
            "hosting": caps.hosting.value,
            "available": ok,
            "unavailable_reason": why,
            "estimated_usd": round(provider.estimate_usd(request), 6),
            "chosen": False,
            "excluded_because": None,
        }
        decision.considered.append(entry)

    def mark(name: str, *, chosen: bool = False, because: str | None = None):
        for e in decision.considered:
            if e["provider"] == name:
                e["chosen"] = chosen
                if because:
                    e["excluded_because"] = because

    local = [p for p in candidates if p.capabilities().hosting is Hosting.LOCAL]
    cloud = [p for p in candidates if p.capabilities().hosting is Hosting.CLOUD]

    # ---- privacy gate, before anything else --------------------------
    if cloud and not is_own_media and not policy.allow_third_party_media_to_cloud:
        for p in cloud:
            mark(p.name, because=(
                "this is third-party media and the policy forbids sending "
                "media the owner does not own to a cloud provider"))
        cloud = []

    # ---- providers the owner has not authorised ----------------------
    unauthorised = [p for p in cloud
                    if p.name not in policy.authorised_providers]
    for p in unauthorised:
        mark(p.name, because=(
            f"{p.name} is not in authorised_providers, so it is not a "
            f"candidate regardless of mode"))
    cloud = [p for p in cloud if p.name in policy.authorised_providers]

    # ---- providers that cannot run -----------------------------------
    def runnable(ps):
        out = []
        for p in ps:
            ok, why = p.available()
            if ok:
                out.append(p)
            else:
                mark(p.name, because=why)
        return out

    local_ready = runnable(local)
    cloud_ready = runnable(cloud)

    # ---- per-request ceiling -----------------------------------------
    affordable = []
    for p in cloud_ready:
        est = p.estimate_usd(request)
        if est > policy.max_usd_per_request:
            mark(p.name, because=(
                f"estimated ${est:.4f} exceeds the per-request ceiling of "
                f"${policy.max_usd_per_request:.4f}"))
        else:
            affordable.append(p)
    cloud_ready = affordable

    # ---- the modes ---------------------------------------------------
    if policy.mode is Mode.LOCAL_ONLY:
        for p in cloud:
            mark(p.name, because="mode is local_only")
        if not local_ready:
            decision.refused = True
            decision.reason = (
                "Mode is local_only and no local provider can run. This is "
                "a refusal, NOT a reason to use a paid provider - a local "
                "failure must never become a charge.")
            return decision
        chosen = local_ready[0]
        mark(chosen.name, chosen=True)
        decision.providers = [chosen.name]
        decision.reason = f"local_only: {chosen.name} runs here, free."
        return decision

    if policy.mode is Mode.CLOUD_ONLY:
        for p in local:
            mark(p.name, because="mode is cloud_only")
        if not cloud_ready:
            decision.refused = True
            decision.reason = (
                "Mode is cloud_only and no authorised, available, "
                "affordable cloud provider exists. Nothing was sent and "
                "nothing was spent.")
            return decision
        if not policy.escalation_authorised:
            decision.refused = True
            decision.requires_authorisation = True
            decision.estimated_usd = round(
                cloud_ready[0].estimate_usd(request), 6)
            decision.reason = (
                f"A cloud provider is configured but paid requests are not "
                f"authorised. Estimated ${decision.estimated_usd:.4f}. "
                f"Authorise spending explicitly to proceed.")
            return decision
        chosen = min(cloud_ready, key=lambda p: p.estimate_usd(request))
        mark(chosen.name, chosen=True)
        decision.providers = [chosen.name]
        decision.estimated_usd = round(chosen.estimate_usd(request), 6)
        decision.requires_authorisation = True
        decision.reason = (
            f"cloud_only: {chosen.name}, the cheapest authorised provider "
            f"that can serve this, at ${decision.estimated_usd:.4f}.")
        return decision

    if policy.mode is Mode.HYBRID:
        if local_ready and local_confidence is None:
            chosen = local_ready[0]
            mark(chosen.name, chosen=True)
            decision.providers = [chosen.name]
            decision.reason = (
                f"hybrid: trying {chosen.name} locally first, free. "
                f"Escalation is considered only after a local result "
                f"exists and only on its measured confidence.")
            return decision

        if local_confidence is not None:
            if local_confidence >= policy.escalate_below_confidence:
                decision.reason = (
                    f"hybrid: the local result's confidence "
                    f"({local_confidence:.2f}) is at or above the "
                    f"escalation threshold "
                    f"({policy.escalate_below_confidence:.2f}), so nothing "
                    f"is escalated and nothing is spent.")
                return decision
            if not policy.escalation_authorised:
                decision.requires_authorisation = True
                decision.estimated_usd = round(
                    min((p.estimate_usd(request) for p in cloud_ready),
                        default=0.0), 6)
                decision.reason = (
                    f"hybrid: the local result is weak "
                    f"(confidence {local_confidence:.2f}) and escalation "
                    f"would help, but paid escalation is not authorised. "
                    f"Estimated ${decision.estimated_usd:.4f} if you "
                    f"authorise it.")
                return decision
            if not cloud_ready:
                decision.reason = (
                    "hybrid: the local result is weak but no authorised, "
                    "available cloud provider can take it. Keeping the "
                    "local answer with its uncertainty recorded.")
                return decision
            chosen = min(cloud_ready, key=lambda p: p.estimate_usd(request))
            mark(chosen.name, chosen=True)
            decision.providers = [chosen.name]
            decision.estimated_usd = round(chosen.estimate_usd(request), 6)
            decision.requires_authorisation = True
            decision.reason = (
                f"hybrid: escalating to {chosen.name} because the local "
                f"confidence was {local_confidence:.2f}, below "
                f"{policy.escalate_below_confidence:.2f}. "
                f"${decision.estimated_usd:.4f}.")
            return decision

        # No local provider could run.
        decision.refused = True
        decision.reason = (
            "hybrid: no local provider can run. Hybrid escalates on a weak "
            "local RESULT, not on a local failure, so this refuses rather "
            "than spending. Fix the local model or choose cloud_only "
            "deliberately.")
        return decision

    # Mode.COMPARE
    picks = list(local_ready)
    if policy.escalation_authorised:
        picks += cloud_ready
    else:
        for p in cloud_ready:
            mark(p.name, because=(
                "compare would include this provider, but paid requests are "
                "not authorised"))
    if not picks:
        decision.refused = True
        decision.reason = "compare: no provider is available to compare."
        return decision
    for p in picks:
        mark(p.name, chosen=True)
    decision.providers = [p.name for p in picks]
    decision.estimated_usd = round(
        sum(p.estimate_usd(request) for p in picks), 6)
    decision.requires_authorisation = any(
        p.capabilities().hosting is Hosting.CLOUD for p in picks)
    decision.reason = (
        f"compare: running {len(picks)} provider(s) on this item for "
        f"evaluation. Agreement between them is NOT ground truth; it is "
        f"only evidence that they share a bias or do not.")
    return decision
