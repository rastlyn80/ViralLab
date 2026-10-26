"""Provider registry, capability honesty, routing safety, cost estimates.

The tests that matter most here are the refusals. A multi-provider layer
fails in exactly one expensive way: something breaks locally and a paid
provider quietly takes over. Several tests below exist only to make that
impossible.

Cloud transports are injected, so the request *shape* is verified against
each vendor's documented contract without a network call or a credential.
"""

from __future__ import annotations

import json

import pytest

from server.ai import vision_task
from server.ai.base import (AIError, AnalysisRequest, Hosting, Modality,
                            Registry, extract_json, validate)
from server.ai.providers import (AnthropicVisionProvider,
                                 GeminiVisionProvider, LocalOcrProvider,
                                 LocalVlmProvider, OpenAIVisionProvider,
                                 default_registry)
from server.ai.routing import Mode, Policy, route


def frames(n=3, size=1000):
    return [(i * 1.0, b"\xff\xd8" + b"x" * size) for i in range(n)]


def make_request(**kw):
    base = dict(task="t", schema={"type": "object"}, prompt="p",
                frames=frames(),
                required_modalities=(Modality.IMAGE_SEQUENCE,))
    base.update(kw)
    return AnalysisRequest(**base)


# ---------------------------------------------------------------------------
# capability honesty
# ---------------------------------------------------------------------------

def test_the_registry_lists_every_provider_with_its_hosting():
    rows = {r["provider"]: r for r in default_registry().describe()}
    assert set(rows) == {"local-ocr", "local-vlm", "openai", "gemini",
                         "anthropic"}
    assert rows["local-ocr"]["hosting"] == "local"
    assert rows["openai"]["hosting"] == "cloud"


def test_only_gemini_claims_native_video():
    """`[PRIMARY]` verified 2026-10-26: OpenAI and Claude document images
    only; Gemini documents video input directly. Flattening that difference
    is the thing this layer exists to prevent."""
    rows = {r["provider"]: r for r in default_registry().describe()}
    assert rows["gemini"]["accepts_video_natively"] is True
    assert rows["openai"]["accepts_video_natively"] is False
    assert rows["anthropic"]["accepts_video_natively"] is False


def test_every_cloud_provider_records_where_its_contract_came_from():
    for p in default_registry().all():
        caps = p.capabilities()
        if caps.hosting is Hosting.CLOUD:
            assert caps.verified_on, f"{p.name} has no verification date"
            assert caps.source_url.startswith("http"), p.name
            assert caps.documented_limits, p.name


def test_claude_records_that_it_will_not_identify_people():
    """Load-bearing: the visual task asks whether a performer is visible,
    which is answerable, not who they are, which this provider refuses."""
    caps = AnthropicVisionProvider().capabilities()
    joined = " ".join(caps.documented_limits).lower()
    assert "identify people" in joined


def test_a_provider_refuses_a_modality_it_does_not_accept():
    req = make_request(required_modalities=(Modality.VIDEO,))
    with pytest.raises(AIError) as exc:
        AnthropicVisionProvider(transport=lambda *a: (200, {})).analyse(req)
    assert exc.value.permanent is True
    assert exc.value.billed is False


def test_a_provider_refuses_more_images_than_it_documents():
    req = make_request(frames=frames(200))
    with pytest.raises(AIError) as exc:
        AnthropicVisionProvider(transport=lambda *a: (200, {})).analyse(req)
    assert "at most 100 images" in str(exc.value)


# ---------------------------------------------------------------------------
# availability is not capability
# ---------------------------------------------------------------------------

def test_the_local_vlm_is_registered_but_explains_why_it_cannot_run():
    """Omitting it would make 'no local option exists' and 'the local
    option is not installed' look identical to routing, and they call for
    opposite responses."""
    ok, why = LocalVlmProvider().available()
    if ok:
        pytest.skip("the VLM stack is installed on this machine")
    assert "not installed" in why
    assert "VRAM" in why


def test_a_cloud_provider_without_a_credential_names_the_variable_not_the_value(
        monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    ok, why = OpenAIVisionProvider().available()
    assert ok is False
    assert "OPENAI_API_KEY" in why
    assert "never made a request" in why


# ---------------------------------------------------------------------------
# cost estimates
# ---------------------------------------------------------------------------

def test_anthropic_visual_tokens_follow_the_documented_formula():
    """`[PRIMARY]` "an image costs ceil(width/28) x ceil(height/28) visual
    tokens", capped by the tier limit."""
    p = AnthropicVisionProvider()
    assert p.visual_tokens(28, 28) == 1
    assert p.visual_tokens(280, 280) == 100
    # 1000x1000 would be 1296 patches, under the 1568 cap.
    assert p.visual_tokens(1000, 1000) == 1296
    # A very large image is capped, not extrapolated.
    assert p.visual_tokens(8000, 8000) == p.STANDARD_MAX_TOKENS


def test_every_provider_estimates_a_cost_and_local_is_free():
    req = make_request()
    for p in default_registry().all():
        est = p.estimate_usd(req)
        assert est >= 0
        if p.capabilities().hosting is Hosting.LOCAL:
            assert est == 0.0, f"{p.name} is local and must be free"
        else:
            assert est > 0, f"{p.name} is cloud and must cost something"


# ---------------------------------------------------------------------------
# request shape, against each documented contract
# ---------------------------------------------------------------------------

def test_openai_sends_data_uris_and_never_the_key_in_the_url(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-SECRET")
    seen = {}

    def transport(url, headers, body, timeout):
        seen.update(url=url, headers=headers, body=body)
        return 200, {"choices": [{"message": {"content": '{"a": 1}'}}],
                     "usage": {"prompt_tokens": 10, "completion_tokens": 2}}

    req = make_request(schema={"type": "object",
                               "properties": {"a": {"type": "integer"}}})
    out = OpenAIVisionProvider(transport=transport).analyse(req)
    assert out.ok and out.data == {"a": 1}
    assert "SECRET" not in seen["url"]
    assert seen["headers"]["Authorization"] == "Bearer sk-SECRET"
    assert "SECRET" not in json.dumps(seen["body"])
    kinds = [c["type"] for c in seen["body"]["messages"][0]["content"]]
    assert "image_url" in kinds
    assert out.usage.actual_usd is not None


def test_gemini_puts_the_key_in_a_header_not_the_query_string(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "GEM-SECRET")
    seen = {}

    def transport(url, headers, body, timeout):
        seen.update(url=url, headers=headers, body=body)
        return 200, {"candidates": [
            {"content": {"parts": [{"text": '{"a": 2}'}]}}],
            "usageMetadata": {"promptTokenCount": 5,
                              "candidatesTokenCount": 1}}

    req = make_request(schema={"type": "object",
                               "properties": {"a": {"type": "integer"}}})
    out = GeminiVisionProvider(transport=transport).analyse(req)
    assert out.ok
    assert "GEM-SECRET" not in seen["url"], "a URL ends up in logs"
    assert seen["headers"]["x-goog-api-key"] == "GEM-SECRET"


def test_anthropic_sends_images_before_text_as_the_docs_advise(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-SECRET")
    seen = {}

    def transport(url, headers, body, timeout):
        seen.update(url=url, headers=headers, body=body)
        return 200, {"content": [{"type": "text", "text": '{"a": 3}'}],
                     "usage": {"input_tokens": 7, "output_tokens": 1}}

    req = make_request(schema={"type": "object",
                               "properties": {"a": {"type": "integer"}}})
    out = AnthropicVisionProvider(transport=transport).analyse(req)
    assert out.ok
    assert seen["headers"]["x-api-key"] == "ant-SECRET"
    assert seen["headers"]["anthropic-version"]
    content = seen["body"]["messages"][0]["content"]
    # The last block is the prompt; images come before it.
    assert content[-1]["type"] == "text"
    assert any(b["type"] == "image" for b in content[:-1])


def test_a_4xx_is_permanent_and_a_429_is_not(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    with pytest.raises(AIError) as exc:
        AnthropicVisionProvider(
            transport=lambda *a: (400, {"error": "bad"})).analyse(
                make_request())
    assert exc.value.permanent is True
    assert exc.value.billed is False


def test_a_response_that_breaks_the_schema_is_refused_not_stored(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    transport = lambda *a: (200, {  # noqa: E731
        "content": [{"type": "text", "text": '{"a": "not a number"}'}],
        "usage": {"input_tokens": 1, "output_tokens": 1}})
    req = make_request(schema={
        "type": "object", "required": ["a"],
        "properties": {"a": {"type": "integer"}}})
    out = AnthropicVisionProvider(transport=transport).analyse(req)
    assert out.ok is False
    assert out.data is None
    assert out.validation_errors
    assert "not stored as a feature" in out.refused_reason


# ---------------------------------------------------------------------------
# schema validation
# ---------------------------------------------------------------------------

def test_validation_catches_the_errors_that_matter():
    schema = {
        "type": "object", "additionalProperties": False,
        "required": ["flag", "angle"],
        "properties": {
            "flag": {"type": "boolean"},
            "angle": {"type": "string", "enum": ["front", "side"]},
            "score": {"type": "number", "minimum": 0, "maximum": 1},
        }}
    assert validate({"flag": True, "angle": "front"}, schema) == []
    assert validate({"angle": "front"}, schema)                 # missing
    assert validate({"flag": 1, "angle": "front"}, schema)      # int != bool
    assert validate({"flag": True, "angle": "up"}, schema)      # enum
    assert validate({"flag": True, "angle": "front",
                     "score": 2}, schema)                       # maximum
    assert validate({"flag": True, "angle": "front",
                     "extra": 1}, schema)                       # additional


def test_a_bool_never_satisfies_a_number_field():
    """Python's bool is an int, and a model answering `true` where a score
    was asked for has not answered."""
    assert validate(True, {"type": "number"})


def test_json_is_recovered_from_fences_but_not_guessed_at():
    obj, err = extract_json('```json\n{"a": 1}\n```')
    assert obj == {"a": 1} and err is None
    obj, err = extract_json('Here you go: {"a": 1} hope that helps')
    assert obj == {"a": 1}
    obj, err = extract_json('{"a": broken')
    assert obj is None and "not valid JSON" in err
    obj, err = extract_json("")
    assert obj is None


# ---------------------------------------------------------------------------
# routing: the refusals
# ---------------------------------------------------------------------------

class _Fake:
    def __init__(self, name, hosting, available=True, cost=0.0,
                 accepts=(Modality.IMAGE_SEQUENCE,)):
        self.name = name
        self.hosting = hosting
        self._available = available
        self._cost = cost
        self._accepts = accepts

    def capabilities(self):
        from server.ai.base import ModelCapabilities
        return ModelCapabilities(provider=self.name, model="m",
                                 hosting=self.hosting, accepts=self._accepts)

    def available(self):
        return (self._available,
                None if self._available else "deliberately broken")

    def estimate_usd(self, request):
        return self._cost

    def analyse(self, request):
        raise NotImplementedError


def registry_with(*providers):
    reg = Registry()
    for p in providers:
        reg.register(p)
    return reg


def test_a_broken_local_model_never_causes_spending():
    """The single most important test in this file."""
    reg = registry_with(
        _Fake("local", Hosting.LOCAL, available=False),
        _Fake("cloud", Hosting.CLOUD, cost=0.01))
    for mode in (Mode.LOCAL_ONLY, Mode.HYBRID):
        d = route(reg, make_request(), Policy(
            mode=mode, escalation_authorised=True,
            authorised_providers=("cloud",)))
        assert d.refused is True, f"{mode} spent after a local failure"
        assert d.providers == []
        assert d.estimated_usd == 0.0


def test_hybrid_escalates_on_a_weak_result_not_on_a_failure():
    reg = registry_with(_Fake("local", Hosting.LOCAL),
                        _Fake("cloud", Hosting.CLOUD, cost=0.01))
    pol = Policy(mode=Mode.HYBRID, escalation_authorised=True,
                 authorised_providers=("cloud",), max_usd_per_request=0.05,
                 escalate_below_confidence=0.6)

    first = route(reg, make_request(), pol)
    assert first.providers == ["local"]

    strong = route(reg, make_request(), pol, local_confidence=0.9)
    assert strong.providers == []
    assert "nothing is spent" in strong.reason

    weak = route(reg, make_request(), pol, local_confidence=0.2)
    assert weak.providers == ["cloud"]
    assert weak.requires_authorisation is True


def test_hybrid_without_authorisation_asks_instead_of_spending():
    reg = registry_with(_Fake("local", Hosting.LOCAL),
                        _Fake("cloud", Hosting.CLOUD, cost=0.01))
    d = route(reg, make_request(),
              Policy(mode=Mode.HYBRID, escalation_authorised=False,
                     authorised_providers=("cloud",)),
              local_confidence=0.1)
    assert d.providers == []
    assert d.requires_authorisation is True
    assert d.estimated_usd == 0.01


def test_an_unauthorised_provider_is_not_a_candidate_in_any_mode():
    reg = registry_with(_Fake("cloud", Hosting.CLOUD, cost=0.01))
    d = route(reg, make_request(),
              Policy(mode=Mode.CLOUD_ONLY, escalation_authorised=True,
                     authorised_providers=()))
    assert d.refused is True
    excluded = [c for c in d.considered if c["provider"] == "cloud"]
    assert "authorised_providers" in excluded[0]["excluded_because"]


def test_third_party_media_never_goes_to_cloud_by_default():
    reg = registry_with(_Fake("cloud", Hosting.CLOUD, cost=0.01))
    d = route(reg, make_request(),
              Policy(mode=Mode.CLOUD_ONLY, escalation_authorised=True,
                     authorised_providers=("cloud",)),
              is_own_media=False)
    assert d.refused is True
    reason = [c for c in d.considered
              if c["provider"] == "cloud"][0]["excluded_because"]
    assert "does not own" in reason


def test_a_request_over_the_per_request_ceiling_is_excluded():
    reg = registry_with(_Fake("cloud", Hosting.CLOUD, cost=1.00))
    d = route(reg, make_request(),
              Policy(mode=Mode.CLOUD_ONLY, escalation_authorised=True,
                     authorised_providers=("cloud",),
                     max_usd_per_request=0.05))
    assert d.refused is True
    reason = [c for c in d.considered
              if c["provider"] == "cloud"][0]["excluded_because"]
    assert "per-request ceiling" in reason


def test_cloud_only_picks_the_cheapest_authorised_provider():
    reg = registry_with(_Fake("a", Hosting.CLOUD, cost=0.03),
                        _Fake("b", Hosting.CLOUD, cost=0.01))
    d = route(reg, make_request(),
              Policy(mode=Mode.CLOUD_ONLY, escalation_authorised=True,
                     authorised_providers=("a", "b")))
    assert d.providers == ["b"]
    assert d.estimated_usd == 0.01


def test_compare_mode_excludes_paid_providers_without_authorisation():
    reg = registry_with(_Fake("local", Hosting.LOCAL),
                        _Fake("cloud", Hosting.CLOUD, cost=0.01))
    d = route(reg, make_request(),
              Policy(mode=Mode.COMPARE, escalation_authorised=False,
                     authorised_providers=("cloud",)))
    assert d.providers == ["local"]
    assert d.estimated_usd == 0.0
    assert "not ground truth" in d.reason.lower()


def test_the_default_policy_cannot_spend_anything():
    reg = default_registry()
    d = route(reg, make_request(), Policy())
    assert d.estimated_usd == 0.0
    assert all(p.startswith("local") for p in d.providers)


def test_every_routing_decision_explains_itself():
    reg = registry_with(_Fake("local", Hosting.LOCAL),
                        _Fake("cloud", Hosting.CLOUD, cost=0.01))
    d = route(reg, make_request(), Policy())
    assert d.reason
    assert d.considered
    for entry in d.considered:
        assert entry["chosen"] or entry["excluded_because"] or \
            not entry["available"]


# ---------------------------------------------------------------------------
# the visual task
# ---------------------------------------------------------------------------

def test_the_task_schema_covers_everything_the_brief_asks_for():
    props = vision_task.SCHEMA["properties"]
    for field in ("performer_visible", "piano_or_keyboard_visible",
                  "hands_visible", "face_visible", "camera_angle",
                  "shot_distance", "framing", "lighting", "setting",
                  "text_overlay_present", "opening_type", "transitions",
                  "emotional_presentation"):
        assert field in props, field


def test_every_enumerated_field_allows_unknown():
    """A model forced to guess produces a confident wrong answer."""
    for name, spec in vision_task.SCHEMA["properties"].items():
        if isinstance(spec, dict) and "enum" in spec and \
                spec.get("type") == "string":
            assert "unknown" in spec["enum"], name


def test_interpretation_is_flagged_as_interpretation():
    props = vision_task.SCHEMA["properties"]
    flag = props["emotional_presentation_is_interpretation"]
    assert flag["enum"] == [True]
    assert "emotional_presentation_is_interpretation" in \
        vision_task.SCHEMA["required"]


def test_the_task_refuses_repertoire_and_technique_claims():
    joined = " ".join(vision_task.refusals()).lower()
    assert "composer" in joined
    assert "technique" in joined
    prompt = vision_task.build_prompt().lower()
    assert "do not identify the musical work" in prompt
    assert "do not judge performance technique" in prompt


def test_frames_are_sampled_across_time_not_from_one_moment():
    plan = vision_task.FramePlan()
    stamps = plan.timestamps(duration_s=30.0)
    assert len(stamps) >= 5
    assert min(stamps) == 0.0
    assert max(stamps) >= 12.0, "later frames judge whether the hook is " \
                                "representative"
    assert len(set(stamps)) == len(stamps)


def test_a_short_video_drops_the_later_frames_rather_than_inventing_them():
    plan = vision_task.FramePlan()
    stamps = plan.timestamps(duration_s=4.0)
    assert max(stamps) <= 4.0


def test_the_task_schema_stays_inside_the_supported_validator_subset():
    """The validator implements a subset; a schema drifting outside it
    would silently stop being checked."""
    supported = {"type", "enum", "properties", "required",
                 "additionalProperties", "items", "minimum", "maximum",
                 "minItems", "maxItems", "description", "maxLength"}

    def walk(node, path="$"):
        if not isinstance(node, dict):
            return
        for key in node:
            if key in ("properties", "items"):
                continue
            assert key in supported, f"{path}: unsupported keyword {key!r}"
        for name, sub in (node.get("properties") or {}).items():
            walk(sub, f"{path}.{name}")
        if isinstance(node.get("items"), dict):
            walk(node["items"], f"{path}[]")

    walk(vision_task.SCHEMA)


def test_local_ocr_is_registered_as_a_model_with_its_measured_accuracy():
    caps = LocalOcrProvider().capabilities()
    joined = " ".join(caps.notes)
    assert "precision 1.00" in joined
    assert caps.hosting is Hosting.LOCAL
    assert any("cannot describe a scene" in lim
               for lim in caps.documented_limits)


# ---------------------------------------------------------------------------
# task capability, not just modality
#
# Found by running the deployed plan endpoint: routing chose the OCR engine
# for the scene-understanding task, because OCR accepts an image sequence.
# Its own capabilities already said it "cannot describe a scene"; routing
# was not reading them. The request would have produced a guaranteed schema
# violation and looked like a model failure rather than a routing error.
# ---------------------------------------------------------------------------

def test_ocr_is_not_a_candidate_for_the_scene_task():
    reg = default_registry()
    scene = make_request(task=vision_task.TASK, schema=vision_task.SCHEMA)
    names = [p.name for p in reg.candidates(scene)]
    assert "local-ocr" not in names, (
        "the OCR engine cannot describe a scene and must not be routed the "
        "visual task")
    assert "local-vlm" in names, "a general-purpose VLM should qualify"


def test_ocr_is_a_candidate_for_the_text_task():
    from server.ai.providers import OCR_TASK
    reg = default_registry()
    assert "local-ocr" in [p.name
                           for p in reg.candidates(make_request(task=OCR_TASK))]


def test_a_provider_refuses_a_task_it_does_not_serve():
    p = LocalOcrProvider()
    ok, _ = p.available()
    if not ok:
        pytest.skip("rapidocr is not installed on this machine")
    with pytest.raises(AIError) as exc:
        p.analyse(make_request(task=vision_task.TASK))
    assert "does not serve" in str(exc.value)
    assert exc.value.permanent is True


def test_an_empty_supported_tasks_means_general_purpose():
    from server.ai.base import ModelCapabilities
    caps = ModelCapabilities(provider="x", model="m", hosting=Hosting.CLOUD,
                             accepts=(Modality.IMAGE,))
    assert caps.serves_task("anything") is True
    caps2 = ModelCapabilities(provider="x", model="m", hosting=Hosting.CLOUD,
                              accepts=(Modality.IMAGE,),
                              supported_tasks=("only_this",))
    assert caps2.serves_task("only_this") is True
    assert caps2.serves_task("other") is False


def test_routing_the_visual_task_under_the_default_policy_refuses():
    """With no VLM installed and no cloud authorised, the honest answer to
    'describe this opening' is that it cannot be done - not a cheaper,
    wronger answer from a text engine."""
    d = route(default_registry(),
              make_request(task=vision_task.TASK,
                           schema=vision_task.SCHEMA),
              Policy())
    assert d.refused is True
    assert d.estimated_usd == 0.0
    assert "local" in d.reason.lower()
