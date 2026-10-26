"""The visual-recognition task: its schema, its sampling, and its limits.

Defined here as data rather than as prose in a prompt, so that the same task
can be sent to a local model, to any of three cloud providers, or to none,
and the answers remain comparable.

**Temporal sampling is part of the task, not a detail.** One frame cannot
stand in for a video: a static keyboard shot that cuts to a face at 2.5 s is
a different creative choice from either alone. Frames are drawn across the
opening window *and* from later sections, and each carries its timestamp, so
the model is asked about a sequence and its answer can be checked against
the frame it supposedly describes.

**What this task may not claim.** Two refusals are built into the schema
rather than left to the prompt:

  * no musical work, composer or piece may be identified from frames. Sheet
    music in shot is not an identification, and
    `docs/MULTIMODAL_PIPELINE.md` §6 records repertoire identification as an
    open research question.
  * no performance-technique judgement - fingering, pedalling, articulation
    - from visual frames. Those are audio properties being guessed at from
    pictures.

`emotional_presentation` and `narrative_structure` are kept because the
brief asks for them, and are typed as interpretations with a mandatory
`*_is_interpretation: true` flag so no consumer can read them as
measurements.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Sampling plan. Frames in the hook at a higher rate than later ones,
#: because the hook is the question; the later frames exist so the model can
#: say whether the opening is representative.
HOOK_SECONDS = 3.0
HOOK_FRAMES = 4
LATER_OFFSETS_S = (5.0, 12.0)
TASK = "hook_visual_v1"

#: Enumerations are closed on purpose. An open-ended "describe the shot"
#: answer cannot be compared between two providers or aggregated across 48
#: videos; a closed set can, and `unknown` is always available so the model
#: is never forced to guess.
CAMERA_ANGLE = ["front", "side", "overhead", "keyboard_level",
                "behind_performer", "angled", "unknown"]
SHOT_DISTANCE = ["extreme_close", "close", "medium", "wide", "very_wide",
                 "unknown"]
FRAMING = ["centered", "rule_of_thirds", "off_center", "split",
           "full_frame_instrument", "unknown"]
LIGHTING = ["natural", "warm", "cool", "dramatic_low_key", "flat_even",
            "backlit", "dark", "unknown"]
SETTING = ["concert_hall", "studio", "home_room", "outdoor", "stage",
           "plain_backdrop", "unknown"]
OPENING_TYPE = ["performance_immediately", "text_statement", "talking_head",
                "slow_reveal", "close_detail", "montage", "unknown"]
TRANSITION = ["none", "hard_cut", "dissolve", "whip_pan", "zoom", "unknown"]

#: Every item carries these. A label with no confidence and no review path
#: becomes treated as a fact (CLAUDE.md §3.3).
_CONF = {
    "type": "number", "minimum": 0.0, "maximum": 1.0,
    "description": "The model's own confidence. Uncalibrated.",
}


def _enum(values, desc):
    return {"type": "string", "enum": values, "description": desc}


SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "performer_visible", "piano_or_keyboard_visible", "hands_visible",
        "face_visible", "camera_angle", "shot_distance", "framing",
        "lighting", "setting", "text_overlay_present", "opening_type",
        "transitions", "opening_is_representative", "confidence",
        "emotional_presentation", "emotional_presentation_is_interpretation",
        "frames_described",
    ],
    "properties": {
        "performer_visible": {"type": "boolean"},
        "piano_or_keyboard_visible": {"type": "boolean"},
        "hands_visible": {"type": "boolean"},
        "face_visible": {
            "type": "boolean",
            "description": ("Whether a human face is visible. NOT who it "
                            "is - identifying people is out of scope and "
                            "at least one provider refuses it outright."),
        },
        "camera_angle": _enum(CAMERA_ANGLE, "Dominant angle in the hook."),
        "shot_distance": _enum(SHOT_DISTANCE, "Dominant shot distance."),
        "framing": _enum(FRAMING, "How the subject sits in the frame."),
        "lighting": _enum(LIGHTING, "Dominant lighting character."),
        "setting": _enum(SETTING, "Where this appears to be."),
        "text_overlay_present": {
            "type": "boolean",
            "description": ("Text the creator added, as distinct from "
                            "printed text already in the scene such as "
                            "sheet music. OCR cannot tell these apart; "
                            "this is the one place the distinction can be "
                            "made."),
        },
        "sheet_music_visible": {"type": "boolean"},
        "opening_type": _enum(OPENING_TYPE,
                              "What the first seconds actually do."),
        "transitions": {
            "type": "array", "maxItems": 8,
            "items": _enum(TRANSITION, "Transition observed, in order."),
        },
        "scene_changes": {"type": "integer", "minimum": 0, "maximum": 50},
        "opening_is_representative": {
            "type": "boolean",
            "description": ("Does the hook look like the rest of the "
                            "video? This is why later frames are sent."),
        },
        "emotional_presentation": {
            "type": ["string", "null"], "maxLength": 200,
            "description": ("How the opening presents itself. An "
                            "INTERPRETATION, not a measurement, and about "
                            "the presentation rather than the performer."),
        },
        "emotional_presentation_is_interpretation": {
            "type": "boolean", "enum": [True],
            "description": ("Must be true. Present so that no consumer can "
                            "read the field above as a measurement."),
        },
        "narrative_structure": {"type": ["string", "null"], "maxLength": 120},
        "confidence": _CONF,
        "uncertain_fields": {
            "type": "array", "maxItems": 20,
            "items": {"type": "string"},
            "description": ("Fields the model is not confident about. "
                            "Naming them is better than a single number, "
                            "and a model that can express uncertainty is "
                            "worth more than one that cannot."),
        },
        "frames_described": {
            "type": "integer", "minimum": 1, "maximum": 32,
            "description": ("How many frames the answer is based on. A "
                            "model claiming to describe 6 frames when 4 "
                            "were sent has not read the input."),
        },
        "notes": {"type": ["string", "null"], "maxLength": 500},
    },
}

PROMPT = """You are analysing the opening of a short vertical music video.

You will be shown several frames, each labelled with its timestamp. Frames
from the first {hook}s are the subject of the analysis; later frames are
provided only so you can judge whether the opening is representative of the
rest of the video.

Answer ONLY with a JSON object matching the required schema. Rules:

1. Use "unknown" for any enumerated field you cannot determine from the
   frames. Guessing is worse than "unknown" and is scored as an error.
2. Do NOT identify the musical work, composer or piece. Sheet music being
   visible is not an identification.
3. Do NOT judge performance technique - fingering, pedalling, articulation.
   Those are audible properties and you are looking at pictures.
4. Do NOT identify any person.
5. `text_overlay_present` means text the creator added on top of the video.
   Printed text already in the scene, such as sheet music markings, is NOT
   a text overlay; record that as `sheet_music_visible`.
6. `emotional_presentation` describes how the opening presents itself, not
   what the performer feels. Keep it under 200 characters, and set
   `emotional_presentation_is_interpretation` to true.
7. List any field you are unsure about in `uncertain_fields`.
8. Set `frames_described` to the number of frames you actually used.
"""


@dataclass
class FramePlan:
    """Which frames to send, and why those."""

    hook_seconds: float = HOOK_SECONDS
    hook_frames: int = HOOK_FRAMES
    later_offsets_s: tuple[float, ...] = LATER_OFFSETS_S

    def timestamps(self, duration_s: float | None) -> list[float]:
        out = [round(i * self.hook_seconds / max(1, self.hook_frames - 1), 2)
               for i in range(self.hook_frames)]
        for off in self.later_offsets_s:
            if duration_s is None or off < duration_s - 0.5:
                out.append(float(off))
        return out


def build_prompt() -> str:
    return PROMPT.format(hook=HOOK_SECONDS)


def refusals() -> list[str]:
    """What this task will not answer, for the interface to display."""
    return [
        "No musical work, composer or piece is identified from frames. "
        "Repertoire identification is an open research question "
        "(docs/MULTIMODAL_PIPELINE.md 6), and sheet music in shot is not "
        "an identification.",
        "No performance technique is judged from frames. Fingering, "
        "pedalling and articulation are audible properties.",
        "No person is identified. One provider refuses this outright and "
        "the task does not ask for it from any of them.",
        "`emotional_presentation` and `narrative_structure` are "
        "interpretations, flagged as such in the data, and describe the "
        "presentation rather than the performer.",
    ]
