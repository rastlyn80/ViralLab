"""The adapters: one local model that genuinely runs, three cloud contracts.

**What actually runs today, and what does not.** Being precise about this is
the point of the capability system, so it is stated here rather than
discovered at runtime:

* `local-ocr` — **runs.** RapidOCR on onnxruntime, CPU, on the server where
  the media is. Measured on 12 human-read frames: presence precision 1.00 /
  recall 1.00, transcription word-recall 0.988.
* `local-vlm` — **registered, unavailable.** Qwen2.5-VL Q4 was benchmarked
  in Phase 0B at 7634 MiB of 8192, but torch, transformers and the weights
  are not installed on either machine, and the GPU is on the Windows box
  while the media is on the server. The adapter reports exactly that rather
  than pretending, and routing treats it as a candidate that cannot run.
* `openai` / `gemini` / `anthropic` — **implemented against verified
  contracts, no credentials present.** Each refuses without its key. The
  transport is injectable so the request shape is tested without a network.

**No adapter ever escalates.** A local failure raises; it does not reach for
a paid provider. That decision belongs to the routing policy and, through
it, to the owner.
"""

from __future__ import annotations

import json
import logging
import time

from server.ai.base import (AIError, AIProvider, AnalysisRequest,
                            AnalysisResult, Hosting, Modality,
                            ModelCapabilities, ProviderUnavailable, Usage,
                            credential)

logger = logging.getLogger("virallab.ai.providers")

#: The text-extraction task. Declared here so the OCR
#: provider can say it serves this and nothing else.
OCR_TASK = "hook_text_v1"

# ---------------------------------------------------------------------------
# local: OCR. A vision model that runs, with measured accuracy.
# ---------------------------------------------------------------------------

class LocalOcrProvider(AIProvider):
    """Text in frames. Not a scene-understanding model, and does not pretend.

    Registered as an AI provider rather than hidden inside the feature
    pipeline because it *is* one: a model, with a version, a confidence and
    a measured error rate. Routing can then prefer it for the text task and
    never consider it for the visual-scene task.
    """

    name = "local-ocr"
    hosting = Hosting.LOCAL

    def capabilities(self) -> ModelCapabilities:
        from shared import hookocr
        return ModelCapabilities(
            provider=self.name, model=hookocr.ENGINE,
            hosting=Hosting.LOCAL,
            accepts=(Modality.IMAGE, Modality.IMAGE_SEQUENCE),
            supported_tasks=(OCR_TASK,),
            structured_output=True,
            documented_limits=(
                "reads text only; it cannot describe a scene, a shot or a "
                "performer",
                "does not distinguish a caption the creator added from "
                "printed text already in the frame, so sheet-music markings "
                "are read as text",
            ),
            verified_on="2026-10-26",
            notes=(
                "measured on this corpus: presence precision 1.00 / recall "
                "1.00 on 12 human-read frames, transcription word-recall "
                "0.988",
                "CPU only, runs where the media already is",
            ))

    def available(self) -> tuple[bool, str | None]:
        try:
            import rapidocr_onnxruntime  # noqa: F401
        except ImportError:
            return False, ("rapidocr-onnxruntime is not installed on this "
                           "machine")
        return True, None

    def estimate_usd(self, request: AnalysisRequest) -> float:
        return 0.0

    def analyse(self, request: AnalysisRequest) -> AnalysisResult:
        ok, why = self.available()
        if not ok:
            raise ProviderUnavailable(why or "unavailable", provider=self.name)
        self.check_request(request)

        import numpy as np
        from PIL import Image
        import io

        from shared import hookocr

        started = time.monotonic()
        frames, stamps = [], []
        for at, blob in request.frames:
            img = Image.open(io.BytesIO(blob)).convert("RGB")
            frames.append(np.asarray(img))
            stamps.append(at)
        if not frames:
            raise AIError("no frames supplied", provider=self.name,
                          permanent=True, billed=False)

        arr = np.stack(frames)
        res = hookocr.analyse_frames(arr, timestamps=stamps)
        payload = {
            "text": " | ".join(e.text for e in res.elements),
            "elements": [
                {"text": e.text, "first_seen_s": e.first_seen_s,
                 "duration_s": e.duration_s, "score": e.mean_score,
                 "height_frac": e.height_frac, "box": list(e.box)}
                for e in res.elements],
            "element_count": len(res.elements),
            "frames_processed": res.frames_processed,
            "mean_confidence": (
                round(sum(e.mean_score for e in res.elements)
                      / len(res.elements), 3) if res.elements else None),
        }
        return self.finish(
            request, json.dumps(payload),
            usage=Usage(images=len(frames), actual_usd=0.0,
                        estimated_usd=0.0),
            latency_s=time.monotonic() - started)


# The real implementation lives in `local_vlm.py`, which owns the runtime
# lifecycle. Re-exported here so the registry stays the single place that
# knows which providers exist.
from server.ai.local_vlm import LocalVlmProvider  # noqa: E402


# ---------------------------------------------------------------------------
# cloud
# ---------------------------------------------------------------------------

class CloudProvider(AIProvider):
    """Shared plumbing: credentials, transport injection, retries."""

    hosting = Hosting.CLOUD
    env_var: str = ""
    #: Injected in tests. Signature: (url, headers, json_body, timeout) ->
    #: (status, parsed_json). Nothing else reaches the network.
    transport = None

    def __init__(self, transport=None):
        self.transport = transport

    def available(self) -> tuple[bool, str | None]:
        if self.transport is not None:
            return True, None
        if not credential(self.env_var):
            return False, (
                f"{self.env_var} is not set. This provider is implemented "
                f"and its contract is tested, but it has never been given a "
                f"credential and has never made a request.")
        return True, None

    def _post(self, url: str, headers: dict, body: dict,
              timeout: float) -> tuple[int, dict]:
        if self.transport is not None:
            return self.transport(url, headers, body, timeout)
        import httpx
        # The credential is in a header, never in the URL or the body, so
        # it cannot reach a log line that records either.
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, headers=headers, json=body)
            try:
                return r.status_code, r.json()
            except ValueError:
                return r.status_code, {"_raw": r.text[:2000]}

    def _request_with_retries(self, url: str, headers: dict, body: dict,
                              timeout: float, attempts: int = 3
                              ) -> dict:
        import random
        last: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                status, data = self._post(url, headers, body, timeout)
            except Exception as exc:                 # noqa: BLE001
                # A transport failure may or may not have billed. `billed`
                # stays None, which every budget path treats as "may have".
                last = AIError(f"{self.name} transport error: {exc}",
                               provider=self.name, billed=None)
            else:
                if status == 429 or status >= 500:
                    last = AIError(
                        f"{self.name} returned {status}", provider=self.name,
                        billed=False if status == 429 else None)
                elif status >= 400:
                    raise AIError(
                        f"{self.name} returned {status}: "
                        f"{json.dumps(data)[:300]}", provider=self.name,
                        permanent=True, billed=False)
                else:
                    return data
            if attempt < attempts:
                delay = random.uniform(0, min(20.0, 2.0 ** attempt))
                logger.warning("%s attempt %d/%d failed (%s); retrying in "
                               "%.1fs", self.name, attempt, attempts, last,
                               delay)
                time.sleep(delay)
        raise last or AIError(f"{self.name} failed", provider=self.name)


class OpenAIVisionProvider(CloudProvider):
    """`[PRIMARY]` developers.openai.com/api/docs/guides/images, 2026-10-26.

    Images only. The docs list no direct video input, so a request for
    `Modality.VIDEO` is refused rather than silently reduced to frames.
    """

    name = "openai"
    env_var = "OPENAI_API_KEY"
    MODEL = "gpt-5.4-mini"
    URL = "https://api.openai.com/v1/chat/completions"
    #: `[ASSUMPTION]` not read from a pricing page in this session; the
    #: estimate is therefore provisional and labelled as such wherever it
    #: is shown.
    USD_PER_MTOK_IN = 0.60
    USD_PER_MTOK_OUT = 2.40

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            provider=self.name, model=self.MODEL, hosting=Hosting.CLOUD,
            accepts=(Modality.IMAGE, Modality.IMAGE_SEQUENCE, Modality.TEXT),
            max_images_per_request=1500,
            max_request_bytes=512 * 1024 * 1024,
            accepts_video_natively=False,
            structured_output=True,
            documented_limits=(
                "no direct video input is documented; frames must be "
                "extracted first",
                "PNG, JPEG, WEBP and non-animated GIF only",
                "up to 30,000 patches per image",
            ),
            usd_per_mtok_in=self.USD_PER_MTOK_IN,
            usd_per_mtok_out=self.USD_PER_MTOK_OUT,
            verified_on="2026-10-26",
            source_url="https://developers.openai.com/api/docs/guides/images",
            notes=("token prices are [ASSUMPTION], not read from a pricing "
                   "page in this session",))

    def estimate_usd(self, request: AnalysisRequest) -> float:
        # Image token cost is not documented as a simple formula on the
        # page that was read, so this estimates from payload size and is
        # deliberately generous. An estimate that is too low is the
        # dangerous direction.
        approx_in = 800 + request.required_images() * 1200
        return round(
            approx_in / 1e6 * self.USD_PER_MTOK_IN
            + request.max_output_tokens / 1e6 * self.USD_PER_MTOK_OUT, 6)

    def analyse(self, request: AnalysisRequest) -> AnalysisResult:
        ok, why = self.available()
        if not ok:
            raise ProviderUnavailable(why or "unavailable", provider=self.name)
        self.check_request(request)
        import base64

        content: list[dict] = [{"type": "text", "text": request.prompt}]
        for at, blob in request.frames:
            content.append({"type": "text", "text": f"Frame at {at:.2f}s:"})
            content.append({
                "type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64,"
                                     + base64.b64encode(blob).decode()}})

        body = {
            "model": self.MODEL,
            "messages": [{"role": "user", "content": content}],
            "max_completion_tokens": request.max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": request.task, "strict": False,
                                "schema": request.schema}},
        }
        key = credential(self.env_var) or "test"
        headers = {"Authorization": f"Bearer {key}",
                   "Content-Type": "application/json"}

        started = time.monotonic()
        data = self._request_with_retries(self.URL, headers, body,
                                          request.timeout_s)
        text = ((data.get("choices") or [{}])[0].get("message") or {}
                ).get("content") or ""
        u = data.get("usage") or {}
        usage = Usage(
            input_tokens=u.get("prompt_tokens"),
            output_tokens=u.get("completion_tokens"),
            images=request.required_images(),
            estimated_usd=self.estimate_usd(request))
        if usage.input_tokens is not None:
            usage.actual_usd = round(
                usage.input_tokens / 1e6 * self.USD_PER_MTOK_IN
                + (usage.output_tokens or 0) / 1e6 * self.USD_PER_MTOK_OUT, 6)
        return self.finish(request, text, usage=usage,
                           latency_s=time.monotonic() - started)


class GeminiVisionProvider(CloudProvider):
    """`[PRIMARY]` ai.google.dev/gemini-api/docs/video-understanding, 2026-10-26.

    The only provider of the three that accepts **video natively** - mp4
    inline under 100 MB, sampled at 1 FPS by default. That is a genuine
    capability difference and the registry exists to express it rather than
    flatten it.
    """

    name = "gemini"
    env_var = "GEMINI_API_KEY"
    MODEL = "gemini-3.6-flash"
    URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
           "{model}:generateContent")
    INLINE_LIMIT_BYTES = 100 * 1024 * 1024
    #: `[ASSUMPTION]` not read from a pricing page in this session.
    USD_PER_MTOK_IN = 0.30
    USD_PER_MTOK_OUT = 1.20

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            provider=self.name, model=self.MODEL, hosting=Hosting.CLOUD,
            accepts=(Modality.IMAGE, Modality.IMAGE_SEQUENCE,
                     Modality.VIDEO, Modality.AUDIO, Modality.TEXT),
            max_request_bytes=self.INLINE_LIMIT_BYTES,
            accepts_video_natively=True,
            video_fps_sampled=1.0,
            structured_output=True,
            documented_limits=(
                "inline video must be under 100 MB; larger files need the "
                "File API",
                "video is sampled at 1 FPS by default, so a 3-second hook "
                "yields about 3 frames unless fps is raised",
                "the video-understanding page does not document "
                "responseSchema, so structured output is requested and then "
                "validated locally",
            ),
            usd_per_mtok_in=self.USD_PER_MTOK_IN,
            usd_per_mtok_out=self.USD_PER_MTOK_OUT,
            verified_on="2026-10-26",
            source_url="https://ai.google.dev/gemini-api/docs/video-understanding",
            notes=("accepts whole video, which no other provider here does",
                   "token prices are [ASSUMPTION]"))

    def estimate_usd(self, request: AnalysisRequest) -> float:
        approx_in = 800 + request.required_images() * 1100
        if request.video_path:
            approx_in += 3000
        return round(
            approx_in / 1e6 * self.USD_PER_MTOK_IN
            + request.max_output_tokens / 1e6 * self.USD_PER_MTOK_OUT, 6)

    def analyse(self, request: AnalysisRequest) -> AnalysisResult:
        ok, why = self.available()
        if not ok:
            raise ProviderUnavailable(why or "unavailable", provider=self.name)
        self.check_request(request)
        import base64

        parts: list[dict] = [{"text": request.prompt}]
        for at, blob in request.frames:
            parts.append({"text": f"Frame at {at:.2f}s:"})
            parts.append({"inline_data": {
                "mime_type": "image/jpeg",
                "data": base64.b64encode(blob).decode()}})

        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "maxOutputTokens": request.max_output_tokens,
                "responseMimeType": "application/json",
            },
        }
        key = credential(self.env_var) or "test"
        # Key in a header, not the query string: a URL ends up in logs.
        headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
        url = self.URL.format(model=self.MODEL)

        started = time.monotonic()
        data = self._request_with_retries(url, headers, body,
                                          request.timeout_s)
        cands = data.get("candidates") or [{}]
        parts_out = (cands[0].get("content") or {}).get("parts") or [{}]
        text = "".join(p.get("text") or "" for p in parts_out)
        um = data.get("usageMetadata") or {}
        usage = Usage(
            input_tokens=um.get("promptTokenCount"),
            output_tokens=um.get("candidatesTokenCount"),
            images=request.required_images(),
            estimated_usd=self.estimate_usd(request))
        if usage.input_tokens is not None:
            usage.actual_usd = round(
                usage.input_tokens / 1e6 * self.USD_PER_MTOK_IN
                + (usage.output_tokens or 0) / 1e6 * self.USD_PER_MTOK_OUT, 6)
        return self.finish(request, text, usage=usage,
                           latency_s=time.monotonic() - started)


class AnthropicVisionProvider(CloudProvider):
    """`[PRIMARY]` platform.claude.com/docs/en/build-with-claude/vision, 2026-10-26.

    Images only; an animation contributes its first frame. Two documented
    limits matter for this project's task: **Claude will not identify
    people**, which is fine for "is a performer visible" and fatal for
    "who", and visual-token cost is an exact published formula, so the cost
    estimate here is computed rather than guessed.
    """

    name = "anthropic"
    env_var = "ANTHROPIC_API_KEY"
    MODEL = "claude-haiku-4-5-20251001"
    URL = "https://api.anthropic.com/v1/messages"
    API_VERSION = "2023-06-01"
    MAX_IMAGE_BYTES = 10 * 1024 * 1024
    MAX_REQUEST_BYTES = 32 * 1024 * 1024
    #: `[PRIMARY]` "Each patch is a 28x28-pixel block ... an image costs
    #: ceil(width/28) x ceil(height/28) visual tokens."
    PATCH = 28
    #: `[PRIMARY]` standard tier: 1,568 px long edge, 1,568 visual tokens.
    STANDARD_MAX_TOKENS = 1568
    #: `[PRIMARY]` "$1 USD per million input tokens" for Haiku 4.5.
    USD_PER_MTOK_IN = 1.00
    USD_PER_MTOK_OUT = 5.00

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            provider=self.name, model=self.MODEL, hosting=Hosting.CLOUD,
            accepts=(Modality.IMAGE, Modality.IMAGE_SEQUENCE, Modality.TEXT),
            max_images_per_request=100,
            max_image_bytes=self.MAX_IMAGE_BYTES,
            max_image_pixels=8000 * 8000,
            max_request_bytes=self.MAX_REQUEST_BYTES,
            accepts_video_natively=False,
            structured_output=False,
            documented_limits=(
                "no video input; an animation contributes only its first "
                "frame",
                "will NOT identify people in images and refuses to - fine "
                "for 'is a performer visible', impossible for 'who'",
                "coordinates and counts are documented as approximate",
                "above 20 images per request a stricter per-image size "
                "limit applies; keep each under 2000 px",
                "may hallucinate on images under 200 px",
            ),
            usd_per_mtok_in=self.USD_PER_MTOK_IN,
            usd_per_mtok_out=self.USD_PER_MTOK_OUT,
            verified_on="2026-10-26",
            source_url=("https://platform.claude.com/docs/en/build-with-"
                        "claude/vision"),
            notes=("visual-token cost is an exact published formula, so the "
                   "estimate below is computed rather than guessed",))

    def visual_tokens(self, width: int, height: int) -> int:
        """`[PRIMARY]` ceil(w/28) x ceil(h/28), capped by the tier limit."""
        import math
        tokens = (math.ceil(width / self.PATCH)
                  * math.ceil(height / self.PATCH))
        return min(tokens, self.STANDARD_MAX_TOKENS)

    def estimate_usd(self, request: AnalysisRequest) -> float:
        import io

        from PIL import Image
        total_in = 400 + len(request.prompt) // 4
        for _at, blob in request.frames:
            try:
                with Image.open(io.BytesIO(blob)) as img:
                    total_in += self.visual_tokens(*img.size)
            except Exception:                        # noqa: BLE001
                total_in += self.STANDARD_MAX_TOKENS
        return round(
            total_in / 1e6 * self.USD_PER_MTOK_IN
            + request.max_output_tokens / 1e6 * self.USD_PER_MTOK_OUT, 6)

    def analyse(self, request: AnalysisRequest) -> AnalysisResult:
        ok, why = self.available()
        if not ok:
            raise ProviderUnavailable(why or "unavailable", provider=self.name)
        self.check_request(request)
        import base64

        for _at, blob in request.frames:
            if len(blob) > self.MAX_IMAGE_BYTES:
                raise AIError(
                    f"an image is {len(blob)} bytes, above the documented "
                    f"{self.MAX_IMAGE_BYTES}", provider=self.name,
                    permanent=True, billed=False)

        # Images before text: the docs state Claude works best that way.
        content: list[dict] = []
        for at, blob in request.frames:
            content.append({"type": "text", "text": f"Frame at {at:.2f}s:"})
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg",
                "data": base64.b64encode(blob).decode()}})
        content.append({"type": "text", "text": request.prompt})

        body = {"model": self.MODEL,
                "max_tokens": request.max_output_tokens,
                "messages": [{"role": "user", "content": content}]}
        key = credential(self.env_var) or "test"
        headers = {"x-api-key": key,
                   "anthropic-version": self.API_VERSION,
                   "content-type": "application/json"}

        started = time.monotonic()
        data = self._request_with_retries(self.URL, headers, body,
                                          request.timeout_s)
        blocks = data.get("content") or []
        text = "".join(b.get("text") or "" for b in blocks
                       if b.get("type") == "text")
        u = data.get("usage") or {}
        usage = Usage(
            input_tokens=u.get("input_tokens"),
            output_tokens=u.get("output_tokens"),
            images=request.required_images(),
            estimated_usd=self.estimate_usd(request))
        if usage.input_tokens is not None:
            usage.actual_usd = round(
                usage.input_tokens / 1e6 * self.USD_PER_MTOK_IN
                + (usage.output_tokens or 0) / 1e6 * self.USD_PER_MTOK_OUT, 6)
        return self.finish(request, text, usage=usage,
                           latency_s=time.monotonic() - started)


def default_registry():
    from server.ai.base import Registry
    reg = Registry()
    reg.register(LocalOcrProvider())
    reg.register(LocalVlmProvider())
    reg.register(OpenAIVisionProvider())
    reg.register(GeminiVisionProvider())
    reg.register(AnthropicVisionProvider())
    return reg
