"""The local vision-language model, actually running.

Replaces the Phase 6B placeholder that could only explain why it could not
run. Gemma 3 4B (Q4_K_M) through llama.cpp's `llama-server`, which exposes
an OpenAI-compatible `/v1/chat/completions` endpoint - so the adapter is
thin and the provider-independent architecture is unchanged.

**Why this model and not the Phase 0B one.** Qwen2.5-VL 7B Q4 measured
7634 MiB of 8192. Measured again on 2026-10-26, the desktop was already
holding 1815 MiB, leaving 6175 MiB free - so the 0B model would not have
loaded at all. Gemma 3 4B Q4_K_M plus its f16 projector occupies
**4034 MiB measured**, leaving 2141 MiB spare.

**Grammar-constrained decoding is not optional here.** Asked for the scene
schema with only `response_format: json_object`, the model returned an
object missing **14 of 17 required fields** in 20.5 s. With the schema
compiled to a GBNF grammar it returned a fully valid object in **3.8 s** -
correct by construction and five times faster, because the grammar prunes
the sampling space. Any provider without this capability must have its
output validated and refused after the fact instead; this one cannot
produce an invalid shape.

The model still has to be prevented from *claiming* things, which a grammar
cannot do. Those refusals live in the task prompt and are checked by the
benchmark, not assumed.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

from server.ai.base import (AIError, AIProvider, AnalysisRequest,
                            AnalysisResult, Hosting, Modality,
                            ModelCapabilities, ProviderUnavailable, Usage)

logger = logging.getLogger("virallab.ai.local_vlm")

#: Where the runtime and weights live. Gitignored: 4.9 GB of binaries and
#: model files have no business in a source repository.
ROOT = Path(os.environ.get("VIRALLAB_LLAMA_ROOT", ".llamacpp"))
SERVER_EXE = ROOT / "bin" / "llama-server.exe"
MODEL = ROOT / "models" / "gemma-3-4b-it-Q4_K_M.gguf"
MMPROJ = ROOT / "models" / "mmproj-model-f16.gguf"

HOST = os.environ.get("VIRALLAB_LLAMA_HOST", "127.0.0.1")
PORT = int(os.environ.get("VIRALLAB_LLAMA_PORT", "8089"))
BASE_URL = f"http://{HOST}:{PORT}"

MODEL_NAME = "gemma-3-4b-it-Q4_K_M"
MODEL_VERSION = "gemma3-4b-q4km-llamacpp-b11898"

#: Measured on this machine, 2026-10-26.
MEASURED_VRAM_MIB = 4034
#: Context the server is started with. 4096 holds the prompt plus several
#: images at the sizes this pipeline sends.
CTX_SIZE = 4096
#: Images per request. Each 512px frame costs roughly 260 prompt tokens on
#: this model, so six frames plus the prompt sits comfortably inside the
#: context. More would not.
MAX_IMAGES = 8
#: Bytes. A frame larger than this is a mistake upstream, not something to
#: send and hope.
MAX_IMAGE_BYTES = 2 * 1024 * 1024


class LlamaServerUnavailable(ProviderUnavailable):
    """The runtime is installed but not reachable."""


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def installed() -> tuple[bool, str | None]:
    """Are the runtime and weights present on this machine?"""
    missing = [str(p) for p in (SERVER_EXE, MODEL, MMPROJ) if not p.exists()]
    if missing:
        return False, (
            f"the local vision runtime is not installed on this machine: "
            f"missing {', '.join(missing)}. Install llama.cpp (CUDA build) "
            f"and the Gemma 3 4B GGUF under {ROOT}/.")
    return True, None


def start_server(*, wait_s: float = 120.0, gpu_layers: int = 99
                 ) -> tuple[bool, str | None]:
    """Start `llama-server` if it is not already listening.

    Idempotent: an already-running server is reused rather than a second
    one started, because two copies would each claim 4 GB of an 8 GB card
    and neither would load.
    """
    ok, why = installed()
    if not ok:
        return False, why
    if _port_open(HOST, PORT):
        # Listening is not the same as ready, and this branch is the one
        # that bit: a server still loading its weights holds the port, so
        # returning here sent a whole batch at a model that answered 503.
        if _model_loaded():
            return True, None
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if _model_loaded():
                return True, None
            time.sleep(1.0)
        return False, (f"llama-server is listening on {HOST}:{PORT} but "
                       f"still loading after {wait_s:.0f}s")

    log = Path(os.environ.get("TEMP", "/tmp")) / "virallab-llama-server.log"
    cmd = [
        str(SERVER_EXE), "-m", str(MODEL), "--mmproj", str(MMPROJ),
        "--host", HOST, "--port", str(PORT),
        "--n-gpu-layers", str(gpu_layers), "--ctx-size", str(CTX_SIZE),
        "--temp", "0.1",
    ]
    logger.info("starting llama-server on %s:%s", HOST, PORT)
    with log.open("ab") as fh:
        subprocess.Popen(cmd, stdout=fh, stderr=fh,
                         creationflags=getattr(subprocess,
                                               "CREATE_NO_WINDOW", 0))
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if _port_open(HOST, PORT) and _model_loaded():
            return True, None
        time.sleep(1.0)
    return False, (f"llama-server did not start within {wait_s:.0f}s; see "
                   f"{log}")


def _model_loaded(timeout: float = 2.0) -> bool:
    """Is the server ready to infer, not merely listening?

    MEASURED, 2026-10-27: it accepts connections several seconds before
    the weights finish loading, and answers requests in that window with
    `503 {"code":503,"type":"unavailable_error","message":"Loading
    model"}`. A readiness check that only opened the socket let a
    benchmark run lose its first **17 of 40** publications to that 503 -
    silently, because each one was recorded as an ordinary per-video
    failure rather than as "the server was not up yet".

    `/health` is the endpoint that distinguishes the two states, so the
    check asks it rather than inferring readiness from the socket.
    """
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"{BASE_URL}/health",
                                    timeout=timeout) as r:
            return r.status == 200
    except urllib.error.HTTPError as exc:
        return exc.code == 200
    except Exception:                      # noqa: BLE001 - not ready yet
        return False


def vram_used_mib() -> int | None:
    """TOTAL VRAM in use on the card, where nvidia-smi is available.

    Total, not this process's share: it includes whatever the desktop and
    other applications hold (1815 MiB on this machine at rest). The model's
    own footprint is the difference across a load, which is how the
    4034 MiB figure in the capabilities was obtained.
    """
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=20)
        return int(out.stdout.strip().splitlines()[0])
    except Exception:                                # noqa: BLE001
        return None


class LocalVlmProvider(AIProvider):
    """Gemma 3 4B via llama.cpp. Real inference, schema-constrained."""

    name = "local-vlm"
    hosting = Hosting.LOCAL

    def __init__(self, *, autostart: bool = True, transport=None):
        #: Injected in tests so the request shape is checked without a GPU.
        self.transport = transport
        self.autostart = autostart

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            provider=self.name, model=MODEL_NAME, hosting=Hosting.LOCAL,
            accepts=(Modality.IMAGE, Modality.IMAGE_SEQUENCE, Modality.TEXT),
            max_images_per_request=MAX_IMAGES,
            max_image_bytes=MAX_IMAGE_BYTES,
            accepts_video_natively=False,
            # The decisive capability: output is constrained to the schema
            # during sampling, so an invalid shape cannot be produced.
            structured_output=True,
            documented_limits=(
                f"measured at {MEASURED_VRAM_MIB} MiB VRAM on an RTX 2070 "
                f"SUPER, so it cannot share the card with another model",
                f"context is {CTX_SIZE} tokens; roughly 260 prompt tokens "
                f"per 512px frame, which is what caps images at "
                f"{MAX_IMAGES}",
                "a grammar guarantees the SHAPE of the answer, not its "
                "truth; accuracy is measured separately in "
                "docs/benchmarks/6c-vision-benchmark.md",
                "4B parameters: weaker than a frontier cloud model at fine "
                "distinctions such as shot framing",
            ),
            verified_on="2026-10-26",
            source_url="https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md",
            notes=(
                "runs under Gemma Terms of Use: commercial use permitted, "
                "outputs are explicitly NOT model derivatives",
                "schema-constrained decoding made the difference between 14 "
                "missing required fields and a valid object, and was 5x "
                "faster",
            ))

    def available(self) -> tuple[bool, str | None]:
        if self.transport is not None:
            return True, None
        ok, why = installed()
        if not ok:
            return False, why
        if _port_open(HOST, PORT):
            return True, None
        if not self.autostart:
            return False, (f"llama-server is not listening on {BASE_URL} and "
                           f"autostart is disabled")
        return True, None

    def estimate_usd(self, request: AnalysisRequest) -> float:
        return 0.0

    def _post(self, body: dict, timeout: float) -> dict:
        if self.transport is not None:
            return self.transport(f"{BASE_URL}/v1/chat/completions", {}, body,
                                  timeout)
        import urllib.error
        import urllib.request
        req = urllib.request.Request(
            f"{BASE_URL}/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:400]
            raise AIError(f"llama-server returned {exc.code}: {detail}",
                          provider=self.name, permanent=exc.code < 500,
                          billed=False) from exc
        except TimeoutError as exc:
            raise AIError(
                f"llama-server did not respond within {timeout:.0f}s. The "
                f"model may be loading, or the request may be too large for "
                f"the {CTX_SIZE}-token context.",
                provider=self.name, billed=False) from exc
        except OSError as exc:
            raise AIError(f"cannot reach llama-server at {BASE_URL}: {exc}",
                          provider=self.name, billed=False) from exc

    def analyse(self, request: AnalysisRequest) -> AnalysisResult:
        ok, why = self.available()
        if not ok:
            raise LlamaServerUnavailable(why or "unavailable",
                                         provider=self.name)
        self.check_request(request)

        if self.transport is None and not _port_open(HOST, PORT):
            started, why = start_server()
            if not started:
                raise LlamaServerUnavailable(why or "could not start",
                                             provider=self.name)

        for at, blob in request.frames:
            if len(blob) > MAX_IMAGE_BYTES:
                raise AIError(
                    f"frame at {at:.2f}s is {len(blob)} bytes, above the "
                    f"{MAX_IMAGE_BYTES} limit", provider=self.name,
                    permanent=True, billed=False)

        content: list[dict] = []
        for at, blob in request.frames:
            content.append({"type": "text", "text": f"Frame at {at:.2f}s:"})
            content.append({"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64,"
                       + base64.b64encode(blob).decode()}})
        content.append({"type": "text", "text": request.prompt})

        body = {
            "model": MODEL_NAME,
            "temperature": 0.1,
            "max_tokens": request.max_output_tokens,
            "messages": [{"role": "user", "content": content}],
            # The load-bearing line. llama.cpp compiles this to a GBNF
            # grammar and constrains sampling, so the response cannot be
            # the wrong shape.
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": request.task,
                                "schema": request.schema}},
        }

        vram_before = vram_used_mib()
        started = time.monotonic()
        data = self._post(body, request.timeout_s)
        latency = time.monotonic() - started
        vram_after = vram_used_mib()

        choices = data.get("choices") or [{}]
        text = (choices[0].get("message") or {}).get("content") or ""
        u = data.get("usage") or {}
        usage = Usage(
            input_tokens=u.get("prompt_tokens"),
            output_tokens=u.get("completion_tokens"),
            images=request.required_images(),
            actual_usd=0.0, estimated_usd=0.0)

        result = self.finish(request, text, usage=usage, latency_s=latency,
                             peak_vram_mib=vram_after or vram_before)
        if result.data is not None:
            # Provenance that belongs with the answer, not beside it.
            result.data.setdefault("_model", MODEL_VERSION)
        return result
