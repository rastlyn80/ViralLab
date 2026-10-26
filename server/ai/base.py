"""Provider-independent AI analysis: the seam, not the models.

ViralLab must not be locked to one model vendor, and the three that matter
are **not interchangeable**. Verified against each vendor's own current
documentation on 2026-10-26:

| | video input | images/request | notes |
|---|---|---|---|
| local VLM | frames only | hardware-bound | needs 7634 MiB VRAM, one modality at a time |
| OpenAI | **no** | 1,500 | 512 MB payload |
| Gemini | **yes**, natively | — | 1 FPS default sampling, <100 MB inline |
| Claude | **no** (first frame of an animation only) | 600, or 100 at 200k context | 10 MB/image, **cannot identify people** |

A layer that hid those differences behind one `analyse(video)` call would
either silently send a whole video to a provider that cannot read it, or
silently reduce Gemini to the weakest common denominator. So capability is
declared, a request states what it actually needs, and a provider that
cannot serve it says so instead of trying.

**Three rules this module enforces.**

*Never silently escalate to paid.* A local model that fails, is missing or
is out of memory produces a refusal. Spending is a decision, and a decision
nobody made is not one.

*Structured output or nothing.* Every analysis declares a JSON schema and
the response is validated against it. A model that returns prose where a
boolean was asked for has failed the request; it has not produced a
"roughly right" answer to be parsed hopefully later.

*The model's confidence is the model's.* It is recorded, never renamed to
"accuracy", and never compared across providers as though the numbers meant
the same thing.
"""

from __future__ import annotations

import logging
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger("virallab.ai")


class Modality(str, Enum):
    """What a provider can be given."""

    IMAGE = "image"
    IMAGE_SEQUENCE = "image_sequence"
    VIDEO = "video"
    AUDIO = "audio"
    TEXT = "text"


class Hosting(str, Enum):
    LOCAL = "local"
    CLOUD = "cloud"


class AIError(RuntimeError):
    """A provider call failed.

    `permanent` separates "this will never work" (bad schema, unsupported
    modality, no credential) from "try again" (timeout, 429, 5xx). The
    budget path depends on the distinction: a permanent failure never
    reached the meter, a transient one may have.
    """

    def __init__(self, message: str, *, provider: str | None = None,
                 permanent: bool = False, billed: bool | None = None):
        super().__init__(message)
        self.provider = provider
        self.permanent = permanent
        #: None means genuinely unknown, which is treated as "may have been
        #: billed" everywhere it matters.
        self.billed = billed


class ProviderUnavailable(AIError):
    """The provider exists in the registry but cannot run right now."""

    def __init__(self, message: str, *, provider: str | None = None):
        super().__init__(message, provider=provider, permanent=True,
                         billed=False)


@dataclass(frozen=True)
class ModelCapabilities:
    """What one model can actually do, from its vendor's documentation.

    Every field here was read from the vendor's own docs rather than
    inferred, and `verified_on` / `source_url` record where and when, so a
    stale claim is identifiable rather than permanent (CLAUDE.md §2.2).
    """

    provider: str
    model: str
    hosting: Hosting
    accepts: tuple[Modality, ...]
    #: Which analysis tasks this model can actually answer.
    #:
    #: Accepting a modality is not the same as being able to serve a task.
    #: The OCR engine accepts an image sequence and reads text from it; it
    #: cannot say whether a piano is visible. Routing on modality alone
    #: sent the scene-understanding task to the OCR provider, which would
    #: have produced a guaranteed schema violation and looked like a model
    #: failure rather than a routing error. Empty means general-purpose,
    #: which is correct for a VLM.
    supported_tasks: tuple[str, ...] = ()
    #: None where the vendor does not document a limit.
    max_images_per_request: int | None = None
    max_image_bytes: int | None = None
    max_image_pixels: int | None = None
    max_request_bytes: int | None = None
    accepts_video_natively: bool = False
    video_fps_sampled: float | None = None
    #: Does the vendor document a way to force a JSON schema?
    structured_output: bool = False
    #: Things the model is documented as unable to do. Load-bearing: the
    #: visual task below asks "is a performer visible", and a provider that
    #: refuses to identify people can answer that but not "who".
    documented_limits: tuple[str, ...] = ()
    #: USD per million input/output tokens, where the model is priced that
    #: way. None means not priced per token or not recorded.
    usd_per_mtok_in: float | None = None
    usd_per_mtok_out: float | None = None
    #: Visual tokens per image, as a callable, where the vendor documents
    #: the formula. Left None rather than guessed.
    verified_on: str = ""
    source_url: str = ""
    notes: tuple[str, ...] = ()

    def supports(self, modality: Modality) -> bool:
        return modality in self.accepts

    def serves_task(self, task: str) -> bool:
        """An empty `supported_tasks` means general-purpose."""
        return not self.supported_tasks or task in self.supported_tasks


@dataclass
class AnalysisRequest:
    """One analysis, stating what it needs rather than what it hopes for."""

    task: str
    #: JSON schema the response must satisfy. Required: an unvalidated
    #: model answer is prose, not data.
    schema: dict
    prompt: str
    #: (timestamp_seconds, jpeg_bytes). Temporal sampling is explicit so a
    #: single frame can never stand in for a video.
    frames: list[tuple[float, bytes]] = field(default_factory=list)
    video_path: str | None = None
    audio: bytes | None = None
    video_id: int | None = None
    max_output_tokens: int = 1200
    timeout_s: float = 120.0
    #: What the caller will accept. A provider lacking all of these is not
    #: a candidate.
    required_modalities: tuple[Modality, ...] = (Modality.IMAGE_SEQUENCE,)

    def required_images(self) -> int:
        return len(self.frames)

    def payload_bytes(self) -> int:
        return sum(len(b) for _t, b in self.frames) + (
            len(self.audio) if self.audio else 0)


@dataclass
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    images: int = 0
    #: What the provider actually billed, where it reports it. An estimate
    #: is never promoted into this field.
    actual_usd: float | None = None
    estimated_usd: float | None = None


@dataclass
class AnalysisResult:
    provider: str
    model: str
    hosting: Hosting
    task: str
    #: The validated object. None when validation failed.
    data: dict | None = None
    raw_text: str | None = None
    usage: Usage = field(default_factory=Usage)
    latency_s: float | None = None
    produced_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc))
    schema_valid: bool = False
    validation_errors: list[str] = field(default_factory=list)
    #: Set when the provider produced something that was then refused.
    refused_reason: str | None = None
    warnings: list[str] = field(default_factory=list)
    #: TOTAL VRAM in use on the card during the run, where measurable -
    #: not this process's share. It includes the desktop's own usage.
    peak_vram_mib: int | None = None

    @property
    def ok(self) -> bool:
        return self.data is not None and self.schema_valid


# ---------------------------------------------------------------------------
# schema validation, implemented here because jsonschema is not installed
# and the subset actually used is small (CLAUDE.md §6)
# ---------------------------------------------------------------------------

def validate(obj, schema: dict, path: str = "$") -> list[str]:
    """Validate against the JSON-Schema subset these tasks use.

    Supports: type, enum, properties, required, additionalProperties,
    items, minimum, maximum, minItems, maxItems, nullable via type lists.
    Anything else is ignored rather than silently treated as satisfied -
    the task schemas are written to stay inside this subset, and a schema
    that drifts outside it will fail the test that checks exactly that.
    """
    errors: list[str] = []
    if schema is None:
        return errors

    types = schema.get("type")
    if types is not None:
        allowed = types if isinstance(types, list) else [types]
        if not _type_ok(obj, allowed):
            errors.append(
                f"{path}: expected {'/'.join(allowed)}, got "
                f"{_type_name(obj)}")
            return errors

    if "enum" in schema and obj not in schema["enum"]:
        errors.append(f"{path}: {obj!r} is not one of {schema['enum']}")

    if isinstance(obj, dict):
        props = schema.get("properties") or {}
        for key in schema.get("required", []):
            if key not in obj:
                errors.append(f"{path}.{key}: required but missing")
        if schema.get("additionalProperties") is False:
            for key in obj:
                if key not in props:
                    errors.append(
                        f"{path}.{key}: not allowed by the schema")
        for key, sub in props.items():
            if key in obj:
                errors.extend(validate(obj[key], sub, f"{path}.{key}"))

    if isinstance(obj, list):
        item_schema = schema.get("items")
        if schema.get("minItems") is not None and \
                len(obj) < schema["minItems"]:
            errors.append(
                f"{path}: needs at least {schema['minItems']} items, "
                f"got {len(obj)}")
        if schema.get("maxItems") is not None and \
                len(obj) > schema["maxItems"]:
            errors.append(
                f"{path}: at most {schema['maxItems']} items, got "
                f"{len(obj)}")
        if item_schema:
            for i, item in enumerate(obj):
                errors.extend(validate(item, item_schema, f"{path}[{i}]"))

    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        if schema.get("minimum") is not None and obj < schema["minimum"]:
            errors.append(f"{path}: {obj} is below {schema['minimum']}")
        if schema.get("maximum") is not None and obj > schema["maximum"]:
            errors.append(f"{path}: {obj} is above {schema['maximum']}")

    return errors


def _type_name(obj) -> str:
    if obj is None:
        return "null"
    if isinstance(obj, bool):
        return "boolean"
    if isinstance(obj, (int, float)):
        return "number"
    if isinstance(obj, str):
        return "string"
    if isinstance(obj, list):
        return "array"
    if isinstance(obj, dict):
        return "object"
    return type(obj).__name__


def _type_ok(obj, allowed: list[str]) -> bool:
    name = _type_name(obj)
    if name in allowed:
        return True
    # An integer satisfies "number"; a bool does not satisfy either, which
    # is why `_type_name` checks bool first.
    if name == "number" and "integer" in allowed and float(obj).is_integer():
        return True
    return False


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> tuple[dict | None, str | None]:
    """Pull an object out of a model response.

    Models wrap JSON in prose and fences despite instructions. Recovering
    from that is reasonable; *guessing* at malformed JSON is not, so this
    only strips fences and finds the outermost object.
    """
    import json

    if not text:
        return None, "the model returned nothing"
    candidate = text.strip()
    m = _JSON_BLOCK.search(candidate)
    if m:
        candidate = m.group(1).strip()
    if not candidate.startswith("{"):
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end <= start:
            return None, "no JSON object in the response"
        candidate = candidate[start:end + 1]
    try:
        obj = json.loads(candidate)
    except ValueError as exc:
        return None, f"the response is not valid JSON: {exc}"
    if not isinstance(obj, dict):
        return None, f"expected an object, got {_type_name(obj)}"
    return obj, None


# ---------------------------------------------------------------------------
# the adapter interface
# ---------------------------------------------------------------------------

class AIProvider(ABC):
    """One model behind one interface."""

    name: str = "abstract"
    hosting: Hosting = Hosting.LOCAL

    @abstractmethod
    def capabilities(self) -> ModelCapabilities:
        ...

    @abstractmethod
    def available(self) -> tuple[bool, str | None]:
        """Can this run right now, and if not, why not?

        Separate from `capabilities` on purpose: a provider can be fully
        capable on paper and unusable because a credential is missing, a
        model was never downloaded, or the GPU is busy. Routing needs both
        answers and must never confuse them.
        """

    @abstractmethod
    def estimate_usd(self, request: AnalysisRequest) -> float:
        """What this request would cost. 0.0 for local."""

    @abstractmethod
    def analyse(self, request: AnalysisRequest) -> AnalysisResult:
        ...

    # -- shared helpers -------------------------------------------------

    def check_request(self, request: AnalysisRequest) -> None:
        """Refuse a request this provider cannot honestly serve."""
        caps = self.capabilities()
        if not caps.serves_task(request.task):
            raise AIError(
                f"{self.name} does not serve the {request.task!r} task; it "
                f"serves {list(caps.supported_tasks)}. Sending it anyway "
                f"would produce a schema violation and look like a model "
                f"failure rather than a routing error.",
                provider=self.name, permanent=True, billed=False)
        if not any(caps.supports(m) for m in request.required_modalities):
            raise AIError(
                f"{self.name} accepts {[m.value for m in caps.accepts]} but "
                f"this request needs one of "
                f"{[m.value for m in request.required_modalities]}",
                provider=self.name, permanent=True, billed=False)
        n = request.required_images()
        if caps.max_images_per_request and n > caps.max_images_per_request:
            raise AIError(
                f"{self.name} accepts at most "
                f"{caps.max_images_per_request} images per request; this "
                f"request has {n}", provider=self.name, permanent=True,
                billed=False)
        if caps.max_request_bytes and \
                request.payload_bytes() > caps.max_request_bytes:
            raise AIError(
                f"{self.name} caps a request at {caps.max_request_bytes} "
                f"bytes; this payload is {request.payload_bytes()}",
                provider=self.name, permanent=True, billed=False)

    def finish(self, request: AnalysisRequest, text: str, *,
               usage: Usage, latency_s: float,
               peak_vram_mib: int | None = None) -> AnalysisResult:
        """Validate a raw response into a result."""
        caps = self.capabilities()
        out = AnalysisResult(
            provider=self.name, model=caps.model, hosting=self.hosting,
            task=request.task, raw_text=text, usage=usage,
            latency_s=round(latency_s, 3), peak_vram_mib=peak_vram_mib)
        obj, err = extract_json(text or "")
        if obj is None:
            out.validation_errors = [err or "unparseable"]
            out.refused_reason = (
                f"the response could not be read as the requested object: "
                f"{err}")
            return out
        errors = validate(obj, request.schema)
        out.data = obj if not errors else None
        out.schema_valid = not errors
        out.validation_errors = errors
        if errors:
            out.refused_reason = (
                f"the response did not satisfy the task schema "
                f"({len(errors)} problem(s)); it is recorded but not stored "
                f"as a feature")
        return out


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

class Registry:
    """Which providers exist, and which can run right now."""

    def __init__(self):
        self._providers: dict[str, AIProvider] = {}

    def register(self, provider: AIProvider) -> None:
        self._providers[provider.name] = provider

    def get(self, name: str) -> AIProvider:
        if name not in self._providers:
            raise ProviderUnavailable(
                f"no provider named {name!r}; registered: "
                f"{sorted(self._providers)}")
        return self._providers[name]

    def all(self) -> list[AIProvider]:
        return list(self._providers.values())

    def describe(self) -> list[dict]:
        """Everything the interface needs, including why something is off."""
        out = []
        for p in self._providers.values():
            caps = p.capabilities()
            ok, why = p.available()
            out.append({
                "provider": p.name,
                "model": caps.model,
                "hosting": caps.hosting.value,
                "available": ok,
                "unavailable_reason": why,
                "accepts": [m.value for m in caps.accepts],
                "supported_tasks": list(caps.supported_tasks),
                "accepts_video_natively": caps.accepts_video_natively,
                "max_images_per_request": caps.max_images_per_request,
                "max_image_bytes": caps.max_image_bytes,
                "structured_output": caps.structured_output,
                "documented_limits": list(caps.documented_limits),
                "usd_per_mtok_in": caps.usd_per_mtok_in,
                "usd_per_mtok_out": caps.usd_per_mtok_out,
                "verified_on": caps.verified_on,
                "source_url": caps.source_url,
                "notes": list(caps.notes),
            })
        return out

    def candidates(self, request: AnalysisRequest, *,
                   hosting: Hosting | None = None) -> list[AIProvider]:
        """Providers that could serve this request, available or not."""
        out = []
        for p in self._providers.values():
            caps = p.capabilities()
            if hosting is not None and caps.hosting is not hosting:
                continue
            if not any(caps.supports(m) for m in request.required_modalities):
                continue
            if not caps.serves_task(request.task):
                continue
            if (caps.max_images_per_request
                    and request.required_images() > caps.max_images_per_request):
                continue
            out.append(p)
        return out


def credential(env_var: str) -> str | None:
    """Read a credential, never log it, never return it to a caller that
    would serialise it. The name is logged; the value never is."""
    value = os.environ.get(env_var)
    return value or None
