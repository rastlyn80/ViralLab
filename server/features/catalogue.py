"""The feature catalogue: one registry, and grades nobody can assert.

M4. Every feature this project can measure, per modality, with its
grade, its method version, the evidence behind the grade, and which
platforms it is available on. The strategist, the report and the
comparisons read this instead of hard-coding lists — which is the whole
point, because a hard-coded list is how a withheld field ends up in a
recommendation.

## A grade is derived, never written down

The five grades come from BLUEPRINT.md §2 and the ladder is strict:

    Unavailable  the data or a reliable measure does not exist
    Observed     a direct measurement on specific publications
    Associated   a correlation that survived correction AND the ad
                 covariate, with n, rho and CI
    Hypothesised suggested by the data but not tested, or n too small
    Validated    survived a pre-registered own-account experiment

None of those is a field in this file. `grade_for()` computes each one
from three things that can be checked: whether an extractor exists and
has produced rows, whether `visual_structure.WITHHELD_VISION` withholds
the field, and what `feature_evidence` holds. Writing a grade by hand
would make the registry a claim; deriving it makes the registry a
measurement, and a stale grade becomes a query rather than a reading.

**Nothing reaches Validated yet and nothing can.** Validated requires
the Phase 8 pre-registered experiment, which has not run. The ladder
implements it so the gap is visible rather than forgotten.

## What `SPEC` is for, given the database already knows

`SPEC` is what *should* exist. The database only knows what *does*.
Those differ in the direction that matters: `audio_tempo_bpm` has no
rows because nothing extracts it yet, and a registry built by scanning
`video_features` would simply not mention it — the gap would be
invisible, which is the opposite of what BLUEPRINT.md §2 asks for when
it says Unavailable features are shown *and the gap is shown*.

So the spec is declarative and the database decides grades, and a test
asserts in both directions: every spec entry that claims an extractor
has rows, and every feature name in `video_features` appears in the
spec. Drift in either direction fails.

## Platform availability is a property of the platform, not of us

`platforms` records where a feature can be obtained at all. An
Instagram-only metric is not "missing" on TikTok, and reporting it as
a gap we could close would be wrong. Where a platform genuinely cannot
supply something, that is recorded against the platform with the
reason, and the catalogue reports Unavailable *for that platform* while
the feature may be Observed elsewhere.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.db.models import FeatureEvidence, VideoFeature
from server.features import visual_structure as vs
from shared import hookbg, hookfeat, hookocr, hookspeech

logger = logging.getLogger("virallab.features.catalogue")

Grade = Literal["Unavailable", "Observed", "Hypothesised", "Associated",
                "Validated"]

#: BLUEPRINT.md §2, in order of strength. `Hypothesised` sits below
#: `Associated` because it is the same claim without the correction
#: having survived.
GRADE_ORDER: tuple[Grade, ...] = (
    "Unavailable", "Observed", "Hypothesised", "Associated", "Validated")

MODALITIES = ("visual", "audio", "text", "repertoire", "metadata")

#: Significance threshold for a corrected association. Applied to the
#: BH-adjusted q, never to a raw p.
Q_THRESHOLD = 0.05

#: Below this, a surviving correlation is reported as Hypothesised
#: rather than Associated however good it looks. Part 5 overturned a
#: finding when n went from 48 to 258; the lesson has a number.
MIN_N_FOR_ASSOCIATED = 100

#: The covariate M1 established as mandatory for any own-account
#: outcome. An association computed without it is not Associated,
#: whatever its q — the two $1/day Instagram ads mean the outcome is
#: contaminated and the correction is not optional (ADR 0008).
AD_COVARIATE = "ad_exposure"


@dataclass(frozen=True)
class PlatformAvailability:
    platform: str
    available: bool
    reason: str = ""


@dataclass(frozen=True)
class FeatureSpec:
    """What a feature is, before any evidence is consulted."""

    name: str
    modality: str
    #: One line a non-specialist can read. Shown in the interface.
    describes: str
    #: The method version its rows carry, or "" when nothing extracts
    #: it yet.
    method_version: str = ""
    #: Where it can be obtained at all. Empty means both platforms.
    platforms: tuple[PlatformAvailability, ...] = ()
    #: Set when no extractor exists. Makes the gap explicit instead of
    #: letting absence look like a zero.
    not_built_reason: str = ""
    #: Free-text provenance: deterministic measurement, model output,
    #: human label, platform metric.
    kind: str = "deterministic"
    #: True for fields that exist but may never enter an analysis.
    #: Mirrors `visual_structure.WITHHELD_VISION`, checked by a test.
    withheld: bool = False


@dataclass
class CatalogueEntry:
    spec: FeatureSpec
    grade: Grade = "Unavailable"
    #: Why this grade and not another, in words, always populated.
    grade_reason: str = ""
    #: Publications with a stored value under the spec's method version.
    coverage: int = 0
    evidence: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        s = self.spec
        return {
            "name": s.name,
            "modality": s.modality,
            "describes": s.describes,
            "kind": s.kind,
            "method_version": s.method_version or None,
            "grade": self.grade,
            "grade_reason": self.grade_reason,
            "coverage": self.coverage,
            "withheld": s.withheld,
            "not_built_reason": s.not_built_reason or None,
            "platforms": [
                {"platform": p.platform, "available": p.available,
                 "reason": p.reason or None} for p in s.platforms],
            "evidence": self.evidence,
        }


def _both(*, note: str = "") -> tuple[PlatformAvailability, ...]:
    return (PlatformAvailability("instagram", True, note),
            PlatformAvailability("tiktok", True, note))


def _only(platform: str, why: str) -> tuple[PlatformAvailability, ...]:
    other = "tiktok" if platform == "instagram" else "instagram"
    return (PlatformAvailability(platform, True),
            PlatformAvailability(other, False, why))


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

HOOK = hookfeat.METHOD_VERSION          # hook-deterministic-v1
BG = hookbg.METHOD_VERSION              # hook-background-v1
OCR = hookocr.METHOD_VERSION            # hook-ocr-v1
SPEECH = hookspeech.METHOD_VERSION      # hook-speech-v2
VISION = "hook-vision-gemma3-4b-v2"

#: Why the seven deterministic placeholders exist at all. `hookfeat`
#: stores them with `method='unavailable'` and a reason rather than
#: omitting them, so a consumer asking "is a piano visible" gets a
#: recorded "cannot say" instead of a missing key it might read as
#: false. The catalogue carries that forward.
_SUPERSEDED = ("A deterministic placeholder stored as Unavailable with "
               "its reason. The vision field of the same name is the "
               "real measurement, and it has its own grade.")

#: Not built. Named here on purpose: a registry assembled by scanning
#: `video_features` would simply not mention these, and BLUEPRINT.md §2
#: requires the gap to be shown rather than inferred from silence.
_TODO_AUDIO = ("Named in the M4 brief as deterministic work still to "
               "do. No extractor exists, so there are no rows and no "
               "grade above Unavailable is reachable.")
_TODO_TEXT = ("Named in the M4 brief as deterministic caption analysis "
              "still to do. The caption text is collected; nothing "
              "classifies it yet.")

SPEC: tuple[FeatureSpec, ...] = (
    # ---- visual, deterministic -------------------------------------
    FeatureSpec("edge_density", "visual",
                "How much fine detail fills the opening frames.", HOOK,
                _both()),
    FeatureSpec("contrast_rms", "visual",
                "Spread of brightness within the frame.", HOOK, _both()),
    FeatureSpec("brightness_mean", "visual",
                "Average brightness of the opening.", HOOK, _both()),
    FeatureSpec("saturation_mean", "visual",
                "Average colour intensity.", HOOK, _both()),
    FeatureSpec("mean_abs_frame_diff", "visual",
                "How much the picture changes frame to frame.", HOOK,
                _both()),
    FeatureSpec("visual_novelty_vs_later", "visual",
                "How different the opening looks from later in the "
                "video.", HOOK, _both()),
    FeatureSpec("cut_count", "visual",
                "Number of hard cuts in the hook window.", HOOK, _both()),
    FeatureSpec("cut_times_s", "visual",
                "When each cut happens, in seconds.", HOOK, _both()),
    FeatureSpec("scene_change_count", "visual",
                "Scene changes detected in the hook.", HOOK, _both()),
    FeatureSpec("camera_motion_class", "visual",
                "Whether the camera is static, panning or handheld.",
                HOOK, _both()),
    FeatureSpec("camera_motion_px_per_frame", "visual",
                "Estimated camera movement per frame.", HOOK, _both()),
    FeatureSpec("camera_motion_source_px", "visual",
                "Pixel scale the motion estimate was made at.", HOOK,
                _both()),
    FeatureSpec("text_region_score", "visual",
                "How strongly the frame suggests text is present. A "
                "proxy, not a reading.", HOOK, _both(), kind="proxy"),
    FeatureSpec("text_present_estimate", "visual",
                "Whether `text_region_score` crosses its threshold.",
                HOOK, _both(), kind="proxy"),

    # ---- visual, background decomposition --------------------------
    FeatureSpec("background_static_fraction", "visual",
                "Share of the frame that does not move.", BG, _both()),
    FeatureSpec("background_edge_density", "visual",
                "Detail in the background alone.", BG, _both()),
    FeatureSpec("background_grey_entropy", "visual",
                "Tonal variety of the background.", BG, _both()),
    FeatureSpec("subject_edge_density", "visual",
                "Detail in the moving subject alone.", BG, _both()),
    FeatureSpec("background_subject_edge_ratio", "visual",
                "Background detail relative to the subject.", BG,
                _both()),

    # ---- visual, model ---------------------------------------------
    FeatureSpec("vision_text_overlay_present", "visual",
                "Whether the creator burned caption text onto the "
                "video.", VISION, _both(), kind="model"),
    FeatureSpec("vision_face_visible", "visual",
                "Whether a human face is visible in the opening.",
                VISION, _both(), kind="model"),
    FeatureSpec("vision_piano_or_keyboard_visible", "visual",
                "Whether piano keys are visible.", VISION, _both(),
                kind="model", withheld=True),
    FeatureSpec("vision_sheet_music_visible", "visual",
                "Whether printed notation is visible.", VISION, _both(),
                kind="model", withheld=True),
    FeatureSpec("vision_hands_visible", "visual",
                "Whether hands are visible.", VISION, _both(),
                kind="model", withheld=True),
    FeatureSpec("vision_performer_visible", "visual",
                "Whether any part of a performer is visible.", VISION,
                _both(), kind="model", withheld=True),
    FeatureSpec("vision_shot_distance", "visual",
                "Close, medium or wide.", VISION, _both(), kind="model",
                withheld=True),
    FeatureSpec("vision_setting", "visual",
                "Concert hall, studio, home room, outdoor.", VISION,
                _both(), kind="model", withheld=True),
    FeatureSpec("vision_camera_angle", "visual",
                "Dominant camera angle in the hook.", VISION, _both(),
                kind="model", withheld=True),
    FeatureSpec("vision_framing", "visual",
                "How the subject sits in the frame.", VISION, _both(),
                kind="model", withheld=True),
    FeatureSpec("vision_lighting", "visual",
                "Dominant lighting character.", VISION, _both(),
                kind="model", withheld=True),
    FeatureSpec("vision_opening_type", "visual",
                "What the first seconds actually do.", VISION, _both(),
                kind="model", withheld=True),
    FeatureSpec("vision_opening_is_representative", "visual",
                "Whether the hook resembles the rest of the video.",
                VISION, _both(), kind="model", withheld=True),
    FeatureSpec("vision_transitions", "visual",
                "Transitions the model reports, in order.", VISION,
                _both(), kind="model", withheld=True),
    FeatureSpec("vision_scene_changes", "visual",
                "Scene changes the model counted.", VISION, _both(),
                kind="model", withheld=True),
    FeatureSpec("vision_emotional_presentation", "visual",
                "How the opening presents itself. An interpretation, "
                "flagged as one, never a measurement.", VISION, _both(),
                kind="interpretation", withheld=True),
    FeatureSpec("vision_confidence", "visual",
                "The model's own confidence. Uncalibrated.", VISION,
                _both(), kind="model", withheld=True),
    FeatureSpec("vision_uncertain_fields", "visual",
                "Fields the model said it was unsure about.", VISION,
                _both(), kind="model", withheld=True),
    FeatureSpec("vision_frames_described", "visual",
                "How many frames the answer claims to be based on. An "
                "input check, not a finding.", VISION, _both(),
                kind="diagnostic", withheld=True),
    FeatureSpec("vision_raw", "visual",
                "The model's whole answer, stored verbatim.", VISION,
                _both(), kind="diagnostic", withheld=True),

    # ---- visual, superseded placeholders ---------------------------
    FeatureSpec("performer_visible", "visual", _SUPERSEDED, HOOK,
                _both(), kind="unavailable"),
    FeatureSpec("piano_visible", "visual", _SUPERSEDED, HOOK, _both(),
                kind="unavailable"),
    FeatureSpec("hands_visible", "visual", _SUPERSEDED, HOOK, _both(),
                kind="unavailable"),
    FeatureSpec("face_visible", "visual", _SUPERSEDED, HOOK, _both(),
                kind="unavailable"),
    FeatureSpec("framing_class", "visual", _SUPERSEDED, HOOK, _both(),
                kind="unavailable"),
    FeatureSpec("emotional_presentation", "visual", _SUPERSEDED, HOOK,
                _both(), kind="unavailable"),
    FeatureSpec("on_screen_text_content", "text", _SUPERSEDED, HOOK,
                _both(), kind="unavailable"),

    # ---- audio -----------------------------------------------------
    FeatureSpec("audio_onset_count", "audio",
                "Note or beat onsets detected in the hook.", HOOK,
                _both()),
    FeatureSpec("audio_rms_mean", "audio",
                "Average loudness of the opening.", HOOK, _both()),
    FeatureSpec("audio_rms_peak", "audio",
                "Loudest moment of the opening.", HOOK, _both()),
    FeatureSpec("audio_attack_time_s", "audio",
                "How quickly the sound reaches its peak.", HOOK,
                _both()),
    FeatureSpec("audio_starts_immediately", "audio",
                "Whether sound begins at the first frame.", HOOK,
                _both()),
    FeatureSpec("first_sound_at_s", "audio",
                "When audible sound first appears.", HOOK, _both()),
    FeatureSpec("audio_novelty_vs_later", "audio",
                "How different the opening sounds from later.", HOOK,
                _both()),
    FeatureSpec("spectral_centroid_hz", "audio",
                "Brightness of the sound, as a frequency.", HOOK,
                _both()),
    FeatureSpec("spectral_flatness_mean", "audio",
                "How noise-like rather than tonal the sound is.", HOOK,
                _both()),
    FeatureSpec("voice_band_ratio", "audio",
                "Energy in the speech frequency band.", HOOK, _both()),
    FeatureSpec("audio_tempo_bpm", "audio",
                "Tempo of the opening in beats per minute.",
                not_built_reason=_TODO_AUDIO, platforms=_both()),
    FeatureSpec("audio_climax_at_s", "audio",
                "When the loudest or densest moment arrives.",
                not_built_reason=_TODO_AUDIO, platforms=_both()),

    # ---- text: OCR -------------------------------------------------
    FeatureSpec("ocr_text_present", "text",
                "Whether any on-screen text was read.", OCR, _both()),
    FeatureSpec("ocr_text", "text",
                "The text that was read.", OCR, _both()),
    FeatureSpec("ocr_word_count", "text",
                "How many words appear on screen.", OCR, _both()),
    FeatureSpec("ocr_element_count", "text",
                "How many separate text elements appear.", OCR, _both()),
    FeatureSpec("ocr_elements", "text",
                "Each text element with its position.", OCR, _both()),
    FeatureSpec("ocr_first_text_at_s", "text",
                "When text first appears.", OCR, _both()),
    FeatureSpec("ocr_text_duration_s", "text",
                "How long text stays on screen.", OCR, _both()),
    FeatureSpec("ocr_text_changes", "text",
                "How many times the on-screen text changes.", OCR,
                _both()),
    FeatureSpec("ocr_max_text_height_frac", "text",
                "Height of the largest text as a share of the frame.",
                OCR, _both()),
    FeatureSpec("ocr_mean_confidence", "text",
                "The reader's own confidence. Uncalibrated.", OCR,
                _both(), kind="diagnostic"),
    FeatureSpec("ocr_text_reading_order", "text",
                "The order the elements were read in.", OCR, _both(),
                kind="diagnostic"),

    # ---- text: speech ----------------------------------------------
    FeatureSpec("speech_present", "text",
                "Whether real speech was detected.", SPEECH, _both()),
    FeatureSpec("spoken_words", "text",
                "Words transcribed, where speech was detected.", SPEECH,
                _both()),
    FeatureSpec("speech_seconds", "text",
                "How many seconds contain speech.", SPEECH, _both()),
    FeatureSpec("first_speech_at_s", "text",
                "When speech first begins.", SPEECH, _both()),
    FeatureSpec("rejected_transcript", "text",
                "A transcript the speech gate refused, kept so a "
                "hallucination can be inspected rather than stored as "
                "fact.", SPEECH, _both(), kind="diagnostic"),
    FeatureSpec("speech_present_estimate", "text",
                "A cheap guess at whether speech exists, from the audio "
                "band alone.", HOOK, _both(), kind="proxy"),

    # ---- text: caption ---------------------------------------------
    FeatureSpec("caption_length_chars", "text",
                "How long the caption is.",
                not_built_reason=_TODO_TEXT, platforms=_both()),
    FeatureSpec("caption_intent_class", "text",
                "Whether the caption asks a question, states something "
                "or calls for an action.",
                not_built_reason=_TODO_TEXT, platforms=_both()),

    # ---- repertoire ------------------------------------------------
    FeatureSpec("repertoire_work", "repertoire",
                "Which piece is being played. Known for own "
                "publications from the performances table; for "
                "competitors only when a caption or the platform's "
                "music metadata states it, never inferred from frames.",
                not_built_reason=(
                    "The `performances` table holds 146 rows of own "
                    "repertoire and nothing reads them into the feature "
                    "store yet. Competitor repertoire needs caption and "
                    "music-metadata parsing that does not exist."),
                platforms=_both()),
    FeatureSpec("repertoire_composer", "repertoire",
                "Which composer, on the same terms as the work.",
                not_built_reason=(
                    "As `repertoire_work`. Never inferred from frames: "
                    "docs/MULTIMODAL_PIPELINE.md §6 records repertoire "
                    "identification from vision as an open research "
                    "question, and `vision_task` refuses it."),
                platforms=_both()),
)


# ---------------------------------------------------------------------------
# Deriving the grade
# ---------------------------------------------------------------------------

#: An evidence row only reaches `Associated` if its analysis is one of
#: these. A run that did not control for ad exposure cannot produce an
#: Associated grade however small its q (ADR 0008).
EXPERIMENT_ANALYSES = ("phase8_experiment",)


def _evidence_grade(rows: list[FeatureEvidence]) -> tuple[Grade, str]:
    """The strongest grade a set of evidence rows supports, and why.

    Order of checks matters and is the conservative one: a feature is
    only lifted by a result that survives EVERY requirement, and
    anything falling short of one lands at `Hypothesised` with the
    requirement it missed named.
    """
    if not rows:
        return "Observed", ("measured, but no association has been "
                            "tested against an outcome")

    for r in rows:
        if r.analysis in EXPERIMENT_ANALYSES:
            return "Validated", (
                f"survived the pre-registered experiment {r.analysis} "
                f"(n={r.n})")

    best: tuple[Grade, str] | None = None
    shortfalls: list[str] = []
    for r in rows:
        controls = list(r.controls or [])
        missing = []
        if r.q_value is None or r.q_value > Q_THRESHOLD:
            shown = "not corrected" if r.q_value is None else r.q_value
            missing.append(f"q={shown}")
        if r.n < MIN_N_FOR_ASSOCIATED:
            missing.append(f"n={r.n} below {MIN_N_FOR_ASSOCIATED}")
        if AD_COVARIATE not in controls:
            missing.append(f"{AD_COVARIATE} not among the controls")
        if not missing:
            if best is None:
                best = ("Associated",
                        f"q={r.q_value:.4g} on n={r.n} against "
                        f"{r.outcome}, controlling for "
                        f"{', '.join(controls)}")
        else:
            shortfalls.append(f"{r.outcome}: " + "; ".join(missing))

    if best:
        return best
    # Something was tested and nothing survived. The measurement is
    # still a measurement, so it stays Observed unless a raw signal
    # exists - which is exactly what Hypothesised is for.
    suggestive = [r for r in rows
                  if r.p_value is not None and r.p_value <= 0.05]
    if suggestive:
        return "Hypothesised", (
            "a raw correlation exists but did not survive every "
            "requirement - " + "; ".join(shortfalls[:3]))
    return "Observed", (
        "measured and tested; no correlation survived - "
        + "; ".join(shortfalls[:3]))


def grade_for(spec: FeatureSpec, *, coverage: int,
              evidence: list[FeatureEvidence],
              stored_unavailable_reason: str = "") -> tuple[Grade, str]:
    """The grade this feature has earned, and the reason in words.

    Pure: everything it needs is passed in, so the ladder can be
    exercised directly without a database.
    """
    if spec.not_built_reason:
        return "Unavailable", spec.not_built_reason
    if spec.kind == "unavailable":
        return "Unavailable", (
            stored_unavailable_reason
            or "stored as unavailable by its own extractor")
    if coverage == 0:
        return "Unavailable", (
            f"an extractor exists ({spec.method_version}) but no "
            f"publication has a stored value")
    if spec.withheld:
        why = vs.WITHHELD_VISION.get(spec.name, "")
        return "Unavailable", (
            "withheld from analysis until a held-out benchmark promotes "
            "it" + (f" - {why}" if why else ""))
    return _evidence_grade(evidence)


def build(session: Session, *,
          population: str | None = None) -> list[CatalogueEntry]:
    """The whole catalogue, graded from what the database holds.

    Three grouped queries rather than one per feature: the registry is
    read on every strategist turn and a per-feature round trip would
    make the cheapest call in the system the slowest.
    """
    cov_rows = session.execute(
        select(VideoFeature.feature, VideoFeature.method_version,
               func.count(func.distinct(VideoFeature.video_id)))
        .where(VideoFeature.status == "ok")
        .group_by(VideoFeature.feature, VideoFeature.method_version)).all()
    coverage = {(f, mv): n for f, mv, n in cov_rows}

    reason_rows = session.execute(
        select(VideoFeature.feature,
               func.min(VideoFeature.unavailable_reason))
        .where(VideoFeature.unavailable_reason.is_not(None))
        .group_by(VideoFeature.feature)).all()
    reasons = {f: r for f, r in reason_rows if r}

    ev_q = select(FeatureEvidence)
    if population:
        ev_q = ev_q.where(FeatureEvidence.population == population)
    by_feature: dict[tuple[str, str], list[FeatureEvidence]] = {}
    for row in session.scalars(ev_q).all():
        by_feature.setdefault((row.feature, row.method_version),
                              []).append(row)

    out: list[CatalogueEntry] = []
    for spec in SPEC:
        key = (spec.name, spec.method_version)
        ev = by_feature.get(key, [])
        n = coverage.get(key, 0)
        grade, why = grade_for(
            spec, coverage=n, evidence=ev,
            stored_unavailable_reason=reasons.get(spec.name, ""))
        out.append(CatalogueEntry(
            spec=spec, grade=grade, grade_reason=why, coverage=n,
            evidence=[{
                "outcome": r.outcome, "population": r.population,
                "analysis": r.analysis, "n": r.n, "rho": r.rho,
                "partial_rho": r.partial_rho, "p_value": r.p_value,
                "q_value": r.q_value,
                "ci": ([r.ci_low, r.ci_high]
                       if r.ci_low is not None else None),
                "controls": list(r.controls or []),
                "unavailable_n": r.unavailable_n,
                "direction": r.direction, "note": r.note,
            } for r in sorted(ev, key=lambda r: (r.outcome, r.analysis))]))
    return out


def summary(entries: list[CatalogueEntry]) -> dict:
    """Counts a reader can check the catalogue against at a glance."""
    by_grade: dict[str, int] = {g: 0 for g in GRADE_ORDER}
    by_modality: dict[str, dict[str, int]] = {}
    for e in entries:
        by_grade[e.grade] = by_grade.get(e.grade, 0) + 1
        m = by_modality.setdefault(e.spec.modality,
                                   {g: 0 for g in GRADE_ORDER})
        m[e.grade] += 1
    return {
        "features": len(entries),
        "by_grade": by_grade,
        "by_modality": by_modality,
        "usable_in_a_recommendation": sum(
            1 for e in entries if e.grade in ("Associated", "Validated")),
        "note": ("`usable_in_a_recommendation` counts Associated and "
                 "Validated only. Observed describes what happened and "
                 "may be reported as description; Hypothesised may only "
                 "become an experiment proposal; Unavailable may never "
                 "be used and its gap is shown (BLUEPRINT.md section 2)."),
    }
