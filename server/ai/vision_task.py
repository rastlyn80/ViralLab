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

import os
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
    # MEASURED, Phase 6C: under grammar-constrained decoding this list is
    # not a minimum, it is the whole answer. Across 24 videos every required
    # field was emitted 24/24 and every optional field 0/24 - the single
    # exception being `uncertain_fields`, which PROMPT rule 7 asks for by
    # name. `sheet_music_visible` spent a whole benchmark run unanswered for
    # exactly this reason, producing a gap that looked like 24 `unknown`s
    # but was really a question never asked. Anything wanted from the model
    # must appear here or be named in the prompt.
    "required": [
        "performer_visible", "piano_or_keyboard_visible", "hands_visible",
        "face_visible", "sheet_music_visible", "camera_angle",
        "shot_distance", "framing", "lighting", "setting",
        "text_overlay_present", "opening_type", "transitions",
        "scene_changes", "opening_is_representative", "confidence",
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
    """The prompt the active arm asks with.

    The variant table is defined below this function, so the lookup goes
    through globals() rather than forcing the table above the prompt text
    it refers to.
    """
    attr = globals()["PROMPT_VARIANTS"][active_variant()][0]
    return globals()[attr].format(hook=HOOK_SECONDS)


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
        "The mood and shape of an opening are read as interpretations "
        "rather than measurements. They are flagged as such in the "
        "data, and they describe how the video presents itself, not "
        "what the performer was feeling.",
    ]


# ---------------------------------------------------------------------------
# M3: the negative-example prompt
# ---------------------------------------------------------------------------

#: The measured failure PROMPT_V2 exists to attack.
#:
#: Gemma 3 4B under `hook-vision-gemma3-4b-v2` scored, on 24 human
#: reference labels (docs/benchmarks/6c-vision-benchmark.md):
#:
#:   piano_or_keyboard_visible   specificity 0.000   (3 negatives, 3 wrong)
#:   sheet_music_visible         specificity 0.235   (13 false positives)
#:
#: Specificity 0.000 is not a borderline score. The model reported a piano
#: in a shot of a man walking through a garden. That is not a perception
#: error - nothing piano-shaped is in the frame - it is the model answering
#: from the prior "this is a pianist's account, so there is a piano"
#: instead of from the pixels.
#:
#: PROMPT_V2 therefore does three things the original did not:
#:
#:   1. states the base rate is NOT the answer, naming the prior explicitly
#:      so the model has something to refuse;
#:   2. gives a visibility criterion per presence field - what must be in
#:      shot, and what specifically does not count;
#:   3. supplies the actual measured false positives as worked negative
#:      examples, including the garden.
#:
#: It does NOT change the schema, the frame plan, the model or the decoding
#: grammar. That is deliberate: a difference measured between the two is
#: then attributable to the prompt and nothing else.
#:
#: **RESULT, 2026-10-27, on the 80-publication held-out set** (full
#: tables in docs/benchmarks/6c-vision-benchmark.md). It works, on the
#: field it was aimed at, and it is still not in production:
#:
#:   sheet_music_visible        specificity 0.193 -> 0.807, lift +0.125
#:                              (18 fixed / 2 broken own, p=0.0004;
#:                               17 / 0 competitor, p<0.001)
#:   piano_or_keyboard_visible  specificity 0.100 -> 0.200, and the one
#:                              case it fixed is the garden - the example
#:                              this prompt itself contains
#:   text_overlay_present       recall 1.000 -> 0.912, lift below the bar
#:   face_visible               specificity 0.808 -> 0.750, below the bar
#:   performer_visible          specificity 1.000 -> 0.400
#:   setting                    accuracy 0.362 -> 0.194
#:
#: The prompt does not sharpen perception; it moves the whole operating
#: point towards "no". That rescues the field drowning in false positives
#: and costs the fields that were already fine. A prompt is selected per
#: run, not per field, so adopting this would gain one promoted field and
#: lose two - and choosing the better arm per field after seeing that
#: table would be selection on the test set. Hence `DEFAULT_VARIANT`
#: below stays `original`, and the route forward is either two inference
#: passes or a prompt that fixes notation without shifting the global
#: threshold. Both are cost questions for M4.
PROMPT_V2 = """You are analysing the opening of a short vertical music video.

You will be shown several frames, each labelled with its timestamp. Frames
from the first {hook}s are the subject of the analysis; later frames are
provided only so you can judge whether the opening is representative of the
rest of the video.

Answer ONLY with a JSON object matching the required schema.

## Report what is IN THE FRAMES, not what the frames are probably about

These clips come from a musician's account, so most of them do contain a
piano, a performer and hands. That is background knowledge and it is NOT
evidence about the clip in front of you. Some of these openings show a
garden, a street, a face against a dark background, a title card, or an
audience - and for those the honest answer to several presence questions
is false.

A previous run of this task answered `piano_or_keyboard_visible: true` for
every single clip, including one showing a man walking through a garden
with no instrument anywhere in shot. It was right 21 times out of 24 by
guessing, and that is exactly the behaviour being measured against here.
Answering from the base rate scores zero.

## Visibility criteria - apply these literally

`piano_or_keyboard_visible`: TRUE only if you can see piano KEYS - the
  black-and-white key pattern - in at least one frame.
  FALSE if: the piano is present but photographed from behind or end-on so
  no keys show; there is a music stand, a bench, a stool or a concert stage
  but no keyboard; the clip is a face, a street, a room or a garden.
  A piano you infer from the setting is not a piano you can see.

`sheet_music_visible`: TRUE only if you can see printed musical NOTATION -
  staves, noteheads - in at least one frame.
  FALSE if: there is a music stand with nothing legible on it; there is a
  book, a page, a screen or a poster whose content is not notation; the
  clip merely looks like a practice session. A previous run reported sheet
  music in 13 clips that had none, mostly from a stand or a white
  rectangle. White paper is not notation.

`face_visible`: TRUE only if a human face is actually resolvable in the
  frame.
  FALSE if: you see the back or side of a head with no features; a figure
  is so distant that the face is a few pixels; the face is cropped out
  above the frame edge.

`hands_visible`: TRUE if hands or fingers are in shot anywhere, on the keys
  or not.

`performer_visible`: TRUE if any part of a person who is performing is in
  shot. Hands alone count.

`text_overlay_present`: TRUE for text the CREATOR added on top of the
  video - captions, titles, subtitles. FALSE for text that was already in
  the scene: notation markings, a poster, a sign, a book cover.

## If you are not sure

For the enumerated fields, answer "unknown" rather than guessing; guessing
is scored as an error. The booleans have no "unknown", so for those name
the field in `uncertain_fields` and give your best literal reading of the
pixels - but a boolean you are unsure of is more often false than true,
because uncertainty usually means the thing is not clearly in shot.

## Other rules

1. Do NOT identify the musical work, composer or piece. Sheet music being
   visible is not an identification.
2. Do NOT judge performance technique - fingering, pedalling, articulation.
   Those are audible properties and you are looking at pictures.
3. Do NOT identify any person.
4. `emotional_presentation` describes how the opening presents itself, not
   what the performer feels. Keep it under 200 characters, and set
   `emotional_presentation_is_interpretation` to true.
5. List any field you are unsure about in `uncertain_fields`.
6. Set `frames_described` to the number of frames you actually used.
"""

#: The two arms of the M3 prompt experiment, and the method version each
#: one's rows are stored under. Both are selectable at runtime because the
#: comparison needs both run over the SAME 40 held-out publications, and
#: redeploying between arms would make "same code, different prompt" a
#: claim rather than a fact.
#:
#:   original          -> hook-vision-gemma3-4b-v2   (control)
#:   negative_example  -> hook-vision-gemma3-4b-v3   (treatment)
PROMPT_VARIANTS = {
    "original": ("PROMPT", "hook-vision-gemma3-4b-v2"),
    "negative_example": ("PROMPT_V2", "hook-vision-gemma3-4b-v3"),
}
#: The pipeline keeps asking with the original prompt until the
#: negative-example arm has actually cleared M3's bar on the held-out set.
#: Switching the default first and measuring afterwards is how an unproven
#: field gets into analysis, which is the thing M3 exists to prevent.
DEFAULT_VARIANT = "original"


def active_variant() -> str:
    """Which arm this process is serving.

    An unknown value is a hard error rather than a silent fall back to the
    default: a typo in the environment would otherwise file treatment rows
    under the control's method version, and the two would be impossible to
    separate afterwards.
    """
    name = os.environ.get("VIRALLAB_VISION_PROMPT", DEFAULT_VARIANT)
    if name not in PROMPT_VARIANTS:
        raise ValueError(
            f"VIRALLAB_VISION_PROMPT={name!r} is not one of "
            f"{sorted(PROMPT_VARIANTS)}")
    return name


def method_version() -> str:
    """The version the active arm's feature rows are stored under."""
    return PROMPT_VARIANTS[active_variant()][1]
