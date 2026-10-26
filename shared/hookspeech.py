"""Speech presence, timing and opening words - from models, not heuristics.

Deliberately a separate module from `shared/hookfeat.py`. That one is
reproducible signal processing; this one asks two models a question and
stores what they say, which is a different kind of claim and gets a
different `method` in storage.

**Why this exists at all.** The first version of the hook extractor shipped
a hand-built speech detector from spectral flatness, zero-crossing rate and
a voice-band ratio. Measured against Silero VAD and Whisper on 12 real
openings from this corpus, it reported speech on **12 of 12** when the truth
was **0 of 12**. Solo piano varies those statistics exactly as much as
speech does. The heuristic was deleted rather than tuned.

**Why a transcript is not enough.** `[PRIMARY, measured 0B and reproduced
here]` Whisper hallucinates on music-only audio rather than returning empty.
On this corpus it produced `'you'`, `'you'`, `'You'` and `'Всем уход!'` for
four piano openings with no speech in them. So two gates apply, in order:

  1. a VAD model must find speech at all;
  2. the resulting text must survive `looks_hallucinated`.

Only then is a transcript stored. The cost of a false negative is a lost
transcript; the cost of a false positive is a fabricated one in a database
that later analysis will treat as observation.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger("virallab.hookspeech")

METHOD_VERSION = "hook-speech-v2"

#: Whisper size. `[PRIMARY, measured 0B]` small runs at 258x realtime
#: against medium's 102x, and for this niche the output is near-worthless
#: either way, so the larger model buys nothing.
MODEL_SIZE = "small"
#: int8 on CPU: the opening window is 3 seconds, so this never needs the GPU
#: and therefore never contends with a vision batch for VRAM.
COMPUTE_TYPE = "int8"
SAMPLE_RATE = 16000

#: Above this, the model itself is saying there is probably no speech.
NO_SPEECH_MAX = 0.50

#: Seconds of audio given to the model, of which only the first
#: `hook_seconds` are reported.
#:
#: v1 transcribed the 3-second hook window alone and every single result
#: was a hallucination, including one confident Korean transcript
#: ("computer computer computer, yes, yes, yes", `no_speech_prob` 0.089) on
#: an Italian pianist's video. The cause is not the gate: Whisper
#: identifies language from roughly 30 seconds of audio, and on 3 seconds
#: over piano it guesses. Giving it a longer run and then keeping only the
#: segments that overlap the hook is both more accurate and no more
#: expensive per video in any way that matters.
CONTEXT_SECONDS = 15.0

#: Below this language-identification confidence the transcript is refused.
#: A model unsure what language it heard has not heard words.
MIN_LANGUAGE_PROB = 0.60

#: A token repeated this many times in a row is the classic ASR loop.
MAX_TOKEN_RUN = 3

#: Transcripts Whisper emits on music. Matched case- and
#: punctuation-insensitively, and only when they are the WHOLE transcript -
#: "thank you for watching this" is a real sentence, "Thank you." alone on a
#: piano clip is the model filling silence.
HALLUCINATION_PHRASES = frozenset({
    "you", "thank you", "thanks for watching", "thank you for watching",
    "music", "bye", "bye bye", "subscribe", "please subscribe",
    "thanks", "okay", "ok", "so", "yeah", "oh", "hmm", "mm",
    "silence", "applause", "beep", "the end",
})
#: Below this many characters, a transcript is treated as too short to be
#: evidence of anything even if it is not a listed phrase.
MIN_TRANSCRIPT_CHARS = 12

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)

_model = None


def looks_hallucinated(text: str) -> tuple[bool, str | None]:
    """Is this transcript a known artefact of running ASR on music?"""
    raw = (text or "").strip()
    if not raw:
        return True, "empty transcript"
    norm = _PUNCT.sub("", raw).strip().lower()
    norm = re.sub(r"\s+", " ", norm)
    if norm in HALLUCINATION_PHRASES:
        return True, (f"the entire transcript is {raw!r}, which Whisper "
                      f"emits on music-only audio")
    if len(norm) < MIN_TRANSCRIPT_CHARS:
        return True, (f"only {len(norm)} characters; too short to be "
                      f"evidence of speech")
    words = norm.split()
    if len(set(words)) == 1 and len(words) > 1:
        return True, f"the transcript is {words[0]!r} repeated {len(words)}x"
    # A token repeated consecutively is the classic decoder loop, and it is
    # what slipped through v1: "computer computer computer, yes, yes, yes".
    run_token, run = None, 0
    for w in words:
        run = run + 1 if w == run_token else 1
        run_token = w
        if run >= MAX_TOKEN_RUN:
            return True, (f"{run_token!r} repeats {run} times in a row, "
                          f"which is a decoder loop rather than speech")
    # Very few distinct tokens across a long transcript is the same failure
    # wearing a different shape.
    if len(words) >= 6 and len(set(words)) <= max(2, len(words) // 4):
        return True, (f"only {len(set(words))} distinct tokens across "
                      f"{len(words)} words")
    return False, None


def _load():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        logger.info("loading faster-whisper %s (%s)", MODEL_SIZE,
                    COMPUTE_TYPE)
        _model = WhisperModel(MODEL_SIZE, device="cpu",
                              compute_type=COMPUTE_TYPE)
    return _model


@dataclass
class SpeechResult:
    method_version: str = METHOD_VERSION
    model: str = f"faster-whisper-{MODEL_SIZE}-{COMPUTE_TYPE}"
    #: Did a VAD model find speech in the window?
    speech_present: bool = False
    #: Seconds from the window start to the first speech, where found.
    first_speech_at_s: float | None = None
    speech_seconds: float = 0.0
    #: [(start, end)] within the window.
    segments: list[tuple[float, float]] = field(default_factory=list)
    #: The model's own estimate that there is NO speech. Lower is more
    #: confident that there is.
    no_speech_prob: float | None = None
    transcript: str | None = None
    language: str | None = None
    language_probability: float | None = None
    #: How much audio the model was given, against the hook window reported.
    context_seconds: float | None = None
    #: Set when a transcript was produced and then rejected. The rejection
    #: is recorded rather than silently dropping the text, because "the
    #: model said something and we refused it" is itself a finding.
    rejected_transcript: str | None = None
    rejection_reason: str | None = None
    extract_seconds: float | None = None
    warnings: list[str] = field(default_factory=list)


def analyse(audio: np.ndarray, *, sample_rate: int = SAMPLE_RATE,
            hook_seconds: float = 3.0) -> SpeechResult:
    """VAD, then transcription only if the VAD found something.

    `audio` should be `CONTEXT_SECONDS` of mono float32 from the start of
    the video - the model needs the context to identify a language - while
    everything reported describes only the first `hook_seconds`.
    """
    import time
    started = time.monotonic()
    out = SpeechResult()

    if audio is None or audio.size == 0:
        out.warnings.append("no audio to analyse")
        out.extract_seconds = 0.0
        return out
    if sample_rate != SAMPLE_RATE:
        out.warnings.append(
            f"expected {SAMPLE_RATE} Hz, got {sample_rate}; resample first")
        out.extract_seconds = 0.0
        return out

    model = _load()
    # `vad_filter` runs Silero VAD before decoding, so a music-only clip is
    # usually rejected before Whisper gets the chance to invent words.
    segments, info = model.transcribe(
        audio.astype(np.float32), language=None, beam_size=1,
        vad_filter=True, condition_on_previous_text=False)
    segments = list(segments)

    out.language = info.language
    out.language_probability = round(float(info.language_probability), 3)
    out.context_seconds = round(float(audio.size) / sample_rate, 2)

    # Only what overlaps the hook window is reported. The rest of the
    # context existed to let the model identify a language.
    in_hook = [s for s in segments if float(s.start) < hook_seconds]
    out.segments = [(round(float(s.start), 2),
                     round(min(float(s.end), hook_seconds), 2))
                    for s in in_hook]
    out.speech_seconds = round(
        sum(max(0.0, e - s) for s, e in out.segments), 2)
    out.no_speech_prob = (round(min(float(s.no_speech_prob)
                                    for s in in_hook), 3)
                          if in_hook else 1.0)

    if not in_hook:
        out.speech_present = False
        out.extract_seconds = round(time.monotonic() - started, 3)
        return out

    text = " ".join(s.text.strip() for s in in_hook).strip()
    bad, reason = looks_hallucinated(text)

    if out.no_speech_prob is not None and out.no_speech_prob > NO_SPEECH_MAX:
        bad = True
        reason = (f"the model's own no-speech probability is "
                  f"{out.no_speech_prob}, above the {NO_SPEECH_MAX} gate")
    elif (out.language_probability is not None
            and out.language_probability < MIN_LANGUAGE_PROB):
        bad = True
        reason = (f"the model is only {out.language_probability} confident "
                  f"the language is {out.language!r}; a model unsure what "
                  f"language it heard has not heard words")

    if bad:
        out.speech_present = False
        out.rejected_transcript = text
        out.rejection_reason = reason
        out.first_speech_at_s = None
        logger.info("rejected a transcript (%s): %r", reason, text[:60])
    else:
        out.speech_present = True
        out.transcript = text
        out.first_speech_at_s = out.segments[0][0] if out.segments else None

    out.extract_seconds = round(time.monotonic() - started, 3)
    return out


def measurements(result: SpeechResult, interval: tuple[float, float]
                 ) -> list[dict]:
    """Shape a `SpeechResult` like `hookfeat.Measurement` dicts.

    Kept here rather than importing `hookfeat`, so the two modules stay
    independent: the deterministic pass must be runnable on a machine with
    no model installed.
    """
    model_note = (f"{result.model}, VAD-gated. A transcript is stored only "
                  f"when a VAD model finds speech AND the text survives a "
                  f"hallucination filter.")
    out = [
        {"name": "speech_present", "value": result.speech_present,
         "method": "model", "interval": list(interval),
         "uncertainty": (round(1.0 - result.no_speech_prob, 3)
                         if result.no_speech_prob is not None else None),
         "uncertainty_kind": "confidence",
         "params": {"model": result.model,
                    "no_speech_prob": result.no_speech_prob,
                    "no_speech_gate": NO_SPEECH_MAX},
         "note": model_note},
        {"name": "first_speech_at_s", "value": result.first_speech_at_s,
         "method": "model", "unit": "s", "interval": list(interval),
         "params": {"model": result.model}},
        {"name": "speech_seconds", "value": result.speech_seconds,
         "method": "model", "unit": "s", "interval": list(interval),
         "params": {"model": result.model}},
        {"name": "spoken_words", "value": result.transcript,
         "method": "model", "interval": list(interval),
         "params": {"model": result.model,
                    "language": result.language,
                    "language_probability": result.language_probability,
                    "context_seconds": result.context_seconds,
                    "min_language_prob": MIN_LANGUAGE_PROB},
         "unavailable_reason": (None if result.transcript else
                                (result.rejection_reason
                                 or "no speech detected")),
         "note": model_note},
    ]
    if result.rejected_transcript:
        out.append({
            "name": "rejected_transcript", "value": result.rejected_transcript,
            "method": "model", "interval": list(interval),
            "params": {"reason": result.rejection_reason},
            "note": "What the model produced before the hallucination "
                    "filter refused it. Kept because a refusal is a "
                    "finding, not an absence."})
    return out
