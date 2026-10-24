# ADR 0003 — Python 3.12 runtime, pinned; and the native-library gotchas

**Date:** 2026-10-24
**Status:** Accepted
**Driver:** Phase 0B environment work — `docs/benchmarks/0B-local-ai.md` §0

## Context

The Windows GPU machine's system Python is **3.14.7**. The ML stack this
project depends on does not publish wheels for it: `faster-whisper` (via
`ctranslate2`), PyTorch and several transitive dependencies had no 3.14
builds available at the time of Phase 0B. Building from source on Windows for
a CUDA-linked package is not a maintainable position for a single-operator
project.

Two further native-library problems surfaced during Phase 0B and cost real
debugging time. Both are recorded here so Phase 1 does not rediscover them.

## Decision

**Pin the GPU worker to Python 3.12** in a dedicated virtual environment
(`.venv312`). **The Ubuntu server runs the distribution Python (3.14).**

*Revised 2026-10-24, after provisioning the server.* This ADR originally
required 3.12 on both machines "for contract parity". Ubuntu 26.04 ships
3.14 and carries no 3.12 package, and the server half has **no ML
dependencies at all** — SQLAlchemy 2.1.6, FastAPI 0.162.0, pydantic 2.14.1
and psycopg all install and pass the full 26-test suite on 3.14. Building
CPython from source on the server would have added a maintenance burden for
no benefit, so the parity requirement is dropped.

The split is safe because `shared/` is pure Pydantic with no version-sensitive
behaviour, and the contract is exercised from both sides by the Phase 1
acceptance run. Should `shared/` ever need a version-specific feature, that
is the signal to revisit — not before.

**On the GPU worker specifically**, do not "upgrade" to the system 3.14
because it is newer — that is where the ML wheels are missing. Revisit only
when `ctranslate2` and the rest publish 3.14 wheels, and re-run the Phase 0B
benchmark before and after any such move.

### Gotcha 1 — CTranslate2 needs cuBLAS/cuDNN on the process PATH

`faster-whisper` fails at first inference, not at import, with:

```
RuntimeError: Library cublas64_12.dll is not found or cannot be loaded
```

The pip wheels `nvidia-cublas-cu12` and `nvidia-cudnn-cu12` ship the DLLs but
place them where the loader does not look. **`os.add_dll_directory()` was not
sufficient** — CTranslate2's loader does not consult directories added that
way. What works is putting both directories on the **process `PATH` before
the process starts**:

```
<venv>/Lib/site-packages/nvidia/cublas/bin
<venv>/Lib/site-packages/nvidia/cudnn/bin
```

The worker's launcher is therefore responsible for setting `PATH`; the
application must not rely on `add_dll_directory` alone. A startup
self-check should verify the DLLs resolve and fail loudly with this ADR's
name if not.

### Gotcha 2 — PyAV 16 broke faster-whisper's internal audio decoder

`faster_whisper`'s `decode_audio()` calls `av.open(..., metadata_errors=...)`,
a kwarg **removed in PyAV 16**:

```
TypeError: open() got an unexpected keyword argument 'metadata_errors'
```

**Decision: never pass a file path to `transcribe()`.** Decode audio
ourselves with ffmpeg to 16 kHz mono, load it as a float32 numpy array, and
pass the array. This sidesteps the incompatibility entirely and has two
independent benefits:

- decode cost is attributed to the decode stage, not hidden inside
  transcription timing (which is how Phase 0B could measure the stages
  separately at all);
- ffmpeg is already a hard dependency and is a better-understood decoder than
  whatever PyAV links against.

Pinning PyAV `<16` was rejected: it fixes one symptom while leaving the
project's audio path dependent on a library it otherwise does not use.

## Consequences

- Two Python versions coexist on the Windows machine. `py -3.12` and the
  venv's interpreter must be used explicitly; a bare `python` resolves to
  3.14 and will fail confusingly. Scripts invoke the venv interpreter by
  path.
- `requirements.txt` pins the ML stack; `ffmpeg`/`ffprobe` paths come from
  configuration, not from `PATH` discovery, because winget installs them to a
  versioned package directory that moves on upgrade.
- Versions in use as of Phase 0B: Python 3.12.10, faster-whisper 1.2.1,
  OpenCV 5.0.0, librosa 1.0.0, numpy 2.5.4, ffmpeg 9.0.2, Ollama with
  `qwen2.5vl:7b`.
