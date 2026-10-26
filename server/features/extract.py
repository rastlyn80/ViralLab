"""Run hook extraction over real publications and store versioned evidence.

Orchestration only: the measuring lives in `shared/hookfeat.py` (signal
processing) and `shared/hookspeech.py` (models). This module decides *which*
videos to measure, writes the results under a method version, and keeps the
run itself on the record.

Two rules it exists to enforce.

**Reprocessing is additive.** Storage is keyed on
(video, feature, method_version). Re-running the same version updates that
version's row; running a new version writes new rows beside the old. There
is no code path that deletes a previous version's evidence, which is what
makes "did the new thresholds change anything" an answerable question.

**Own-account only.** Feature extraction reads a local media file, and the
only media this system retains is the owner's own. A competitor publication
has no file here and must never acquire one as a side effect of analysis
(`docs/MULTIMODAL_PIPELINE.md`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.db.models import (Creator, MetricsSnapshot, Video, VideoFeature,
                              VideoFeatureRun)
from shared import hookfeat

logger = logging.getLogger("virallab.features.extract")

KIND_DETERMINISTIC = "hook_deterministic"
KIND_SPEECH = "hook_speech"
KIND_BACKGROUND = "hook_background"


class ExtractionRefused(RuntimeError):
    """Extraction was not run, and why."""


@dataclass
class ExtractOutcome:
    run_id: int | None = None
    kind: str = KIND_DETERMINISTIC
    method_version: str = hookfeat.METHOD_VERSION
    requested: int = 0
    done: int = 0
    failed: int = 0
    features_written: int = 0
    compute_seconds: float = 0.0
    skipped_existing: int = 0
    errors: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# choosing what to measure
# ---------------------------------------------------------------------------

def analysable(session: Session, *, platform: str = "instagram"
               ) -> list[Video]:
    """Own-account publications with a preserved file on disk."""
    return list(session.scalars(
        select(Video)
        .join(Creator, Creator.id == Video.creator_id)
        .where(Video.platform == platform,
               Creator.is_own_account.is_(True),
               Video.media_status == "available",
               Video.media_path.isnot(None))
        .order_by(Video.posted_at.desc())).all())


def with_retention(session: Session, *, platform: str = "instagram"
                   ) -> list[int]:
    """Video ids that have a retention metric AND a preserved file.

    This is the population any hook-versus-retention question can actually
    use. It is much smaller than the preserved corpus, and the difference
    is the single most important number in this phase.
    """
    rows = session.execute(
        select(Video.id)
        .join(Creator, Creator.id == Video.creator_id)
        .join(MetricsSnapshot, MetricsSnapshot.video_id == Video.id)
        .where(Video.platform == platform,
               Creator.is_own_account.is_(True),
               Video.media_status == "available",
               MetricsSnapshot.raw.has_key("reels_skip_rate"))  # noqa: W601
        .distinct()).all()
    return [r[0] for r in rows]


def representative_sample(session: Session, n: int = 12, *,
                          platform: str = "instagram") -> list[Video]:
    """A small sample spanning the retention range, for manual validation.

    Deliberately NOT the top N or the most recent N. Part of the point of
    a validation sample is to contain the cases the extractor will get
    wrong, and those are not concentrated at one end.
    """
    ids = set(with_retention(session, platform=platform))
    if not ids:
        return []
    rows = session.execute(
        select(Video, MetricsSnapshot.raw["reels_skip_rate"].astext.cast(
            __import__("sqlalchemy").Float))
        .join(MetricsSnapshot, MetricsSnapshot.video_id == Video.id)
        .where(Video.id.in_(ids),
               MetricsSnapshot.raw.has_key("reels_skip_rate"))  # noqa: W601
        .order_by(Video.id,
                  MetricsSnapshot.captured_at.desc())).all()
    best: dict[int, tuple[Video, float]] = {}
    for video, skip in rows:
        if video.id not in best and skip is not None:
            best[video.id] = (video, float(skip))
    ordered = sorted(best.values(), key=lambda p: p[1])
    if len(ordered) <= n:
        return [v for v, _ in ordered]
    # Even strides across the sorted skip-rate range.
    step = (len(ordered) - 1) / (n - 1)
    picked = [ordered[round(i * step)][0] for i in range(n)]
    seen, out = set(), []
    for v in picked:
        if v.id not in seen:
            seen.add(v.id)
            out.append(v)
    return out


# ---------------------------------------------------------------------------
# running
# ---------------------------------------------------------------------------

def _store(session: Session, video: Video, run: VideoFeatureRun,
           measurements: list, *, method_version: str,
           model: str | None = None) -> int:
    """Write one video's measurements under one method version."""
    written = 0
    for m in measurements:
        md = m if isinstance(m, dict) else {
            "name": m.name, "value": m.value, "method": m.method,
            "unit": m.unit, "interval": m.interval,
            "uncertainty": m.uncertainty,
            "uncertainty_kind": m.uncertainty_kind,
            "unavailable_reason": m.unavailable_reason,
            "params": m.params, "note": m.note}

        row = session.scalar(select(VideoFeature).where(
            VideoFeature.video_id == video.id,
            VideoFeature.feature == md["name"],
            VideoFeature.method_version == method_version))
        if row is None:
            row = VideoFeature(video_id=video.id, feature=md["name"],
                               method_version=method_version)
            session.add(row)

        value = md.get("value")
        # Clear all four, then set exactly one: a rerun that now produces a
        # number where it used to produce text must not leave both.
        row.value_num = row.value_bool = row.value_text = row.value_json = None
        if isinstance(value, bool):
            row.value_bool = value
        elif isinstance(value, (int, float)):
            fv = float(value)
            if fv != fv or fv in (float("inf"), float("-inf")):
                # NaN and the infinities are not measurements. They reach
                # here from a degenerate input — a silent clip, an empty
                # spectrum — and storing one would be worse than storing
                # nothing: `effective_value` would hand it to a correlation
                # as an ordinary float, and NaN propagates through every
                # statistic it touches without raising anything.
                row.value_num = None
                md = dict(md)
                md["method"] = "unavailable"
                md["unavailable_reason"] = (
                    f"non-finite value ({value!r}) from a degenerate input; "
                    f"not stored as a measurement")
            else:
                row.value_num = fv
        elif isinstance(value, str):
            row.value_text = value
        elif isinstance(value, (list, dict)):
            row.value_json = {"items": value} if isinstance(value, list) \
                else value

        iv = md.get("interval")
        row.media_sha256 = video.media_sha256
        row.media_path = video.media_path
        row.unit = md.get("unit")
        row.method = md["method"]
        row.model = model
        row.uncertainty = md.get("uncertainty")
        row.uncertainty_kind = md.get("uncertainty_kind")
        row.interval_start_s = iv[0] if iv else None
        row.interval_end_s = iv[1] if iv else None
        row.params = md.get("params") or None
        row.note = md.get("note")
        row.unavailable_reason = md.get("unavailable_reason")
        row.extracted_at = datetime.now(timezone.utc)
        row.run_id = run.id
        row.status = "ok"
        row.error = None
        written += 1
    return written


def run_deterministic(session: Session, videos: list[Video], *,
                      hook_seconds: float = hookfeat.HOOK_SECONDS,
                      media_root: Path | None = None) -> ExtractOutcome:
    """Measure the opening of each video with signal processing only."""
    import time

    out = ExtractOutcome(kind=KIND_DETERMINISTIC,
                         method_version=hookfeat.METHOD_VERSION,
                         requested=len(videos))
    run = VideoFeatureRun(
        method_version=hookfeat.METHOD_VERSION, kind=KIND_DETERMINISTIC,
        status="running", videos_requested=len(videos),
        params={"hook_seconds": hook_seconds,
                "frame_fps": hookfeat.FRAME_FPS,
                "analysis_width": hookfeat.ANALYSIS_WIDTH,
                "audio_sr": hookfeat.AUDIO_SR})
    session.add(run)
    session.flush()
    out.run_id = run.id

    started = time.monotonic()
    for video in videos:
        path = Path(video.media_path or "")
        if media_root is not None and not path.is_absolute():
            path = media_root / path.name
        if not path.exists():
            out.failed += 1
            out.errors.append({"video_id": video.id,
                               "external_id": video.external_id,
                               "error": f"file missing: {path}"})
            continue
        try:
            feats = hookfeat.extract_hook(path, hook_seconds=hook_seconds)
        except hookfeat.ExtractionError as exc:
            out.failed += 1
            out.errors.append({"video_id": video.id,
                               "external_id": video.external_id,
                               "error": str(exc)[:300]})
            logger.warning("hook extraction failed for %s: %s",
                           video.external_id, exc)
            continue
        out.features_written += _store(
            session, video, run, feats.measurements,
            method_version=hookfeat.METHOD_VERSION)
        out.done += 1
        # Commit per video: a crash half way through a 258-file run must
        # leave the completed work, not lose it.
        session.commit()

    out.compute_seconds = round(time.monotonic() - started, 2)
    run.videos_done = out.done
    run.videos_failed = out.failed
    run.features_written = out.features_written
    run.compute_seconds = out.compute_seconds
    run.finished_at = datetime.now(timezone.utc)
    run.status = ("ok" if out.failed == 0
                  else ("failed" if out.done == 0 else "partial"))
    if out.errors:
        run.notes = f"{len(out.errors)} failure(s); first: " \
                    f"{out.errors[0]['error'][:200]}"
    session.commit()
    logger.info("hook extraction run %s: %d done, %d failed, %d features, "
                "%.1fs", run.id, out.done, out.failed, out.features_written,
                out.compute_seconds)
    return out


def run_speech(session: Session, videos: list[Video], *,
               hook_seconds: float = hookfeat.HOOK_SECONDS,
               media_root: Path | None = None) -> ExtractOutcome:
    """VAD-gated speech detection and transcription.

    Separate pass and separate `method_version`, because it depends on a
    model and the deterministic pass must stay runnable without one.
    """
    import time

    from shared import hookspeech

    out = ExtractOutcome(kind=KIND_SPEECH,
                         method_version=hookspeech.METHOD_VERSION,
                         requested=len(videos))
    run = VideoFeatureRun(
        method_version=hookspeech.METHOD_VERSION, kind=KIND_SPEECH,
        status="running", videos_requested=len(videos),
        params={"hook_seconds": hook_seconds,
                "context_seconds": hookspeech.CONTEXT_SECONDS,
                "model": hookspeech.MODEL_SIZE,
                "compute_type": hookspeech.COMPUTE_TYPE,
                "no_speech_gate": hookspeech.NO_SPEECH_MAX,
                "min_language_prob": hookspeech.MIN_LANGUAGE_PROB})
    session.add(run)
    session.flush()
    out.run_id = run.id

    started = time.monotonic()
    model_name = f"faster-whisper-{hookspeech.MODEL_SIZE}-" \
                 f"{hookspeech.COMPUTE_TYPE}"
    for video in videos:
        path = Path(video.media_path or "")
        if media_root is not None and not path.is_absolute():
            path = media_root / path.name
        if not path.exists():
            out.failed += 1
            out.errors.append({"video_id": video.id,
                               "error": f"file missing: {path}"})
            continue
        try:
            # Decode the CONTEXT window, not the hook window: Whisper
            # identifies language from far more than 3 seconds, and on 3
            # seconds over music it guesses (measured - v1 produced a
            # confident Korean transcript on an Italian pianist).
            audio = hookfeat.decode_audio(
                path, start=0.0, seconds=hookspeech.CONTEXT_SECONDS)
            result = hookspeech.analyse(audio, hook_seconds=hook_seconds)
        except Exception as exc:                        # noqa: BLE001
            out.failed += 1
            out.errors.append({"video_id": video.id,
                               "error": f"{type(exc).__name__}: {exc}"[:300]})
            logger.warning("speech analysis failed for %s: %s",
                           video.external_id, exc)
            continue
        ms = hookspeech.measurements(result, (0.0, hook_seconds))
        out.features_written += _store(
            session, video, run, ms,
            method_version=hookspeech.METHOD_VERSION, model=model_name)
        out.done += 1
        session.commit()

    out.compute_seconds = round(time.monotonic() - started, 2)
    run.videos_done = out.done
    run.videos_failed = out.failed
    run.features_written = out.features_written
    run.compute_seconds = out.compute_seconds
    run.finished_at = datetime.now(timezone.utc)
    run.status = ("ok" if out.failed == 0
                  else ("failed" if out.done == 0 else "partial"))
    session.commit()
    return out


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------

def features_for(session: Session, video_id: int, *,
                 method_version: str | None = None) -> list[VideoFeature]:
    stmt = select(VideoFeature).where(VideoFeature.video_id == video_id)
    if method_version:
        stmt = stmt.where(VideoFeature.method_version == method_version)
    return list(session.scalars(
        stmt.order_by(VideoFeature.method_version, VideoFeature.feature)
    ).all())


def feature_matrix(session: Session, feature_names: list[str], *,
                   method_versions: list[str] | None = None
                   ) -> dict[int, dict]:
    """{video_id: {feature: effective_value}} for analysis.

    Uses the human correction where one exists - that is the whole point of
    storing corrections - and records nothing about which, because the
    caller that needs provenance should read the rows.
    """
    stmt = select(VideoFeature).where(
        VideoFeature.feature.in_(feature_names),
        VideoFeature.status == "ok")
    if method_versions:
        stmt = stmt.where(VideoFeature.method_version.in_(method_versions))
    out: dict[int, dict] = {}
    for row in session.scalars(stmt).all():
        out.setdefault(row.video_id, {})[row.feature] = row.effective_value
    return out


def correct(session: Session, feature_id: int, *, value,
            by: str = "human", note: str | None = None) -> VideoFeature:
    """Record a human correction beside the machine value, never over it."""
    row = session.get(VideoFeature, feature_id)
    if row is None:
        raise ExtractionRefused(f"no such feature row {feature_id}")
    row.corrected_value_num = None
    row.corrected_value_bool = None
    row.corrected_value_text = None
    if isinstance(value, bool):
        row.corrected_value_bool = value
    elif isinstance(value, (int, float)):
        row.corrected_value_num = float(value)
    elif value is not None:
        row.corrected_value_text = str(value)
    row.corrected_by = by
    row.corrected_at = datetime.now(timezone.utc)
    row.correction_note = note
    session.flush()
    logger.info("feature %s (%s on video %s) corrected by %s",
                feature_id, row.feature, row.video_id, by)
    return row


def coverage(session: Session) -> dict:
    """What has been analysed, and what that leaves."""
    analysable_n = session.scalar(
        select(func.count(Video.id))
        .join(Creator, Creator.id == Video.creator_id)
        .where(Video.platform == "instagram",
               Creator.is_own_account.is_(True),
               Video.media_status == "available")) or 0
    done = session.scalar(
        select(func.count(func.distinct(VideoFeature.video_id)))
        .where(VideoFeature.method_version == hookfeat.METHOD_VERSION)) or 0
    speech_done = session.scalar(
        select(func.count(func.distinct(VideoFeature.video_id)))
        .where(VideoFeature.feature == "speech_present")) or 0
    corrected = session.scalar(
        select(func.count(VideoFeature.id))
        .where(VideoFeature.corrected_at.isnot(None))) or 0
    return {
        "analysable": analysable_n,
        "deterministic_done": done,
        "speech_done": speech_done,
        "with_retention": len(with_retention(session)),
        "corrections": corrected,
        "method_version": hookfeat.METHOD_VERSION,
    }


def run_background(session: Session, videos: list[Video], *,
                   hook_seconds: float = hookfeat.HOOK_SECONDS,
                   media_root: Path | None = None) -> ExtractOutcome:
    """Measure background complexity, separately from the v1 hook features.

    Kept out of `run_deterministic` on purpose. Adding measures to
    `hookfeat` would bump `hook-deterministic-v1` and invalidate 258 videos
    of existing evidence to answer one question about 48 of them. A separate
    method version costs nothing and keeps the v1 rows meaningful.
    """
    import time

    from shared import hookbg

    out = ExtractOutcome(kind=KIND_BACKGROUND,
                         method_version=hookbg.METHOD_VERSION,
                         requested=len(videos))
    run = VideoFeatureRun(
        method_version=hookbg.METHOD_VERSION, kind=KIND_BACKGROUND,
        status="running", videos_requested=len(videos),
        params={"hook_seconds": hook_seconds,
                "static_std": hookbg.STATIC_STD,
                "min_static_fraction": hookbg.MIN_STATIC_FRACTION,
                "edge_mag": hookbg.EDGE_MAG,
                "entropy_bins": hookbg.ENTROPY_BINS})
    session.add(run)
    session.flush()
    out.run_id = run.id

    started = time.monotonic()
    for video in videos:
        path = Path(video.media_path or "")
        if media_root is not None and not path.is_absolute():
            path = media_root / path.name
        if not path.exists():
            out.failed += 1
            out.errors.append({"video_id": video.id,
                               "external_id": video.external_id,
                               "error": f"file missing: {path}"})
            continue
        try:
            feats = hookbg.extract_background(path, hook_seconds=hook_seconds)
        except hookfeat.ExtractionError as exc:
            out.failed += 1
            out.errors.append({"video_id": video.id,
                               "external_id": video.external_id,
                               "error": str(exc)[:300]})
            logger.warning("background extraction failed for %s: %s",
                           video.external_id, exc)
            continue
        out.features_written += _store(
            session, video, run, feats.measurements,
            method_version=hookbg.METHOD_VERSION)
        out.done += 1
        session.commit()

    out.compute_seconds = round(time.monotonic() - started, 2)
    run.videos_done = out.done
    run.videos_failed = out.failed
    run.features_written = out.features_written
    run.compute_seconds = out.compute_seconds
    run.finished_at = datetime.now(timezone.utc)
    run.status = ("ok" if out.failed == 0
                  else ("failed" if out.done == 0 else "partial"))
    if out.errors:
        run.notes = f"{len(out.errors)} failure(s); first: "                     f"{out.errors[0]['error'][:200]}"
    session.commit()
    logger.info("background run %s: %d done, %d failed, %d features, %.1fs",
                run.id, out.done, out.failed, out.features_written,
                out.compute_seconds)
    return out
