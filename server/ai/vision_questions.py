"""Everything the system may ask about a frame, and how far to trust it.

## Why this file exists

The vision stack asked twenty questions per video and was allowed to
use the answers to two of them. Six had been measured and withheld;
the other twelve - camera angle, framing, lighting, pacing, how the
opening presents itself - were extracted on every publication and then
used for nothing at all, because the only switch available was a
boolean in `visual_structure.WITHHELD_VISION`: a field was either
trusted for statistics or invisible.

That binary is the whole problem. "Is this measurement correct often
enough to build a statistical claim on?" and "is this measurement
worth showing a person?" are different questions with different
answers, and collapsing them into one flag threw away eighteen
answers out of twenty.

So a question here carries a **reliability** grade, and reliability is
about the measurement, not about any association:

* `unverified` - nobody has checked it. Not shown, not used.
* `indicative` - shown to a person as the model's reading, clearly
  marked as such. **Never enters a statistic, a finding, or a
  comparison.** No accuracy bar, because it makes no claim.
* `verified` - measured against labels it was not tuned on and cleared
  the pre-registered bar. May enter an association, where the separate
  evidence ladder in `features/catalogue.py` then takes over.

Promotion runs one way: `unverified` -> `indicative` when a question is
worth a person's attention, `indicative` -> `verified` only on a
holdout. Nothing is born verified.

## Why so many questions

Asking is nearly free and checking is expensive, which is the opposite
of how the five original booleans were budgeted. Open-vocabulary
detection answers "is there a music stand in shot" with no training,
no labels and no new code - just a text query - so the cost of a new
question is inference time. The cost of *trusting* it is a labelled
holdout.

The efficient order is therefore: ask widely, show honestly, and spend
the labelling effort only on the questions that turn out to move with
performance. `catalogue.py` already corrects for testing many things
at once, which is what makes that order sound rather than a fishing
expedition.

## What this file is not

It is not an extractor. It records what may be asked, how it would be
answered, and how far the answer may travel. `vision_task.py` owns the
VLM schema, `visual_structure.py` owns what reaches an analysis, and a
test asserts the three agree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

#: How far an answer may travel. See the module docstring.
Reliability = Literal["unverified", "indicative", "verified"]

#: How the answer is obtained. `detector` means open-vocabulary
#: detection (OWLv2), which can be asked for an object it has never
#: been trained on; `vlm` is the captioning model under a grammar;
#: `deterministic` is arithmetic on pixels; `ocr` is text recognition.
Method = Literal["detector", "vlm", "deterministic", "ocr", "composite"]

#: What shape the answer takes.
Answer = Literal["boolean", "enum", "count", "number", "text"]


@dataclass(frozen=True)
class Question:
    """One thing that may be asked about a video's frames."""

    name: str
    #: The question in the words a person would use, not the field
    #: name. This is what the interface shows.
    asks: str
    answer: Answer
    method: Method
    reliability: Reliability = "unverified"
    #: For `enum`, the closed vocabulary. Closed on purpose: an
    #: open-ended answer cannot be aggregated across publications.
    values: tuple[str, ...] = ()
    #: For `detector`, the text queries to score. Several phrasings
    #: because a single one is a lottery.
    queries: tuple[str, ...] = ()
    #: Why it sits at its current reliability. Always populated for
    #: anything not `unverified`, so a reader can see the basis.
    basis: str = ""
    #: True where the answer is a reading rather than an observation -
    #: mood, intent, how something "presents". These may never become
    #: `verified`, because there is no fact to check them against.
    interpretive: bool = False
    #: Questions needing a deliberate negative in the holdout, with a
    #: note on what that negative has to look like. Empty where the
    #: corpus supplies negatives naturally.
    needs_negatives: str = ""


def _det(name: str, asks: str, *queries: str, **kw) -> Question:
    return Question(name=name, asks=asks, answer="boolean",
                    method="detector", queries=queries, **kw)


# ---------------------------------------------------------------------------
# 1. What is in shot
#
# Open-vocabulary detection. Each of these is a text query away, which
# is why the list is long: the old five-boolean schema was a limit of
# the captioning approach, not of this one.
# ---------------------------------------------------------------------------

IN_SHOT: tuple[Question, ...] = (
    _det("piano_or_keyboard_visible",
         "Can you see the piano keys?",
         "a piano keyboard", "piano keys", "black and white keys",
         reliability="unverified",
         basis=("Measured on the M3 corpus and the result was "
                "uninterpretable: only 4 and 6 of 80 publications lack a "
                "piano, so specificity rests on a handful of videos. "
                "Unverified means unchecked, not failed. See "
                "docs/benchmarks/7-detector-comparison.md."),
         needs_negatives=("videos with no keyboard in frame at all - a "
                          "performer's face, an audience, a title card - "
                          "and separately, videos where a piano IS present "
                          "but its keys are not, which is the distinction "
                          "the detector currently cannot make")),
    _det("hands_visible", "Are the performer's hands in shot?",
         "a human hand", "hands", "fingers on a keyboard",
         needs_negatives=("openings framed on a face, an instrument alone, "
                          "a title card or an empty stage")),
    _det("face_visible", "Is a face visible?",
         "a human face", "a person's face",
         reliability="verified",
         basis=("0.808 specificity over 80 held-out publications, "
                "+0.175 lift, pre-registered bar cleared. Weaker on the "
                "owner's own videos (0.750) than on competitors' (0.857) "
                "- recorded in DEVELOPMENT_PLAN section 11.")),
    _det("sheet_music_visible", "Is there sheet music in shot?",
         "sheet music", "a page of musical notation", "a music score",
         basis=("Best result in the project - OWLv2-base scores 0.90 and "
                "0.79 specificity with +0.20 and +0.10 lift on 29 and 28 "
                "negatives - and still unverified, because the corpus "
                "behind that number was spent choosing the prompt in M3 "
                "and the model in Phase 1. A holdout has to say it "
                "independently.")),
    _det("music_stand_visible", "Is there a music stand?",
         "a music stand", "a sheet music stand"),
    _det("second_person_visible", "Is anyone else in shot?",
         "two people", "a group of people", "an audience"),
    _det("audience_visible", "Can you see an audience?",
         "an audience watching", "rows of seated people"),
    _det("other_instrument_visible", "Is another instrument in shot?",
         "a violin", "a cello", "a guitar", "an orchestra",
         "a brass instrument"),
    _det("microphone_visible", "Is a microphone in shot?",
         "a microphone", "a studio microphone on a stand"),
    _det("recording_gear_visible", "Is recording equipment in shot?",
         "a video camera", "a smartphone on a tripod", "studio lights"),
    _det("screen_visible", "Is there a screen or projection?",
         "a television screen", "a projected image", "a computer monitor"),
    _det("window_visible", "Is there a window?",
         "a window", "daylight through a window"),
    _det("candles_or_flowers_visible", "Are there candles or flowers?",
         "lit candles", "a vase of flowers", "a bouquet"),
    _det("chandelier_visible", "Is there a chandelier?",
         "a chandelier", "an ornate ceiling light"),
    _det("pet_or_child_visible", "Is there a pet or a child?",
         "a dog", "a cat", "a young child"),
)


# ---------------------------------------------------------------------------
# 2. How it is shot
#
# The captioning model under a closed vocabulary. These twelve have
# been extracted on every publication since M3 and used for nothing;
# marking them `indicative` is what makes them visible.
# ---------------------------------------------------------------------------

CRAFT: tuple[Question, ...] = (
    Question("camera_angle", "What angle is it shot from?", "enum", "vlm",
             reliability="indicative",
             values=("front", "side", "overhead", "keyboard_level",
                     "behind_performer", "angled", "unknown"),
             basis=("Extracted on every publication since M3 and never "
                    "checked against a label. Shown as the model's reading "
                    "because a reading is useful; barred from statistics "
                    "because it is unchecked.")),
    Question("shot_distance", "How close is the camera?", "enum", "vlm",
             reliability="unverified",
             values=("extreme_close", "close", "medium", "wide",
                     "very_wide", "unknown"),
             basis=("Measured and withheld: accuracy 0.418 against a 0.519 "
                    "commonest-class baseline, lift -0.101. The model "
                    "answers 'medium' for most openings. This one is "
                    "genuinely failed rather than merely unchecked.")),
    Question("framing", "How is the subject placed in frame?", "enum", "vlm",
             reliability="indicative",
             values=("centered", "rule_of_thirds", "off_center", "split",
                     "full_frame_instrument", "unknown"),
             basis="Extracted since M3, never checked against a label."),
    Question("camera_movement", "Does the camera move?", "enum", "vlm",
             values=("static", "slow_push", "pan", "handheld", "orbit",
                     "unknown")),
    Question("depth_of_field", "Is the background blurred?", "enum", "vlm",
             values=("shallow", "deep", "unknown")),
    Question("orientation", "Is it vertical, square or landscape?",
             "enum", "deterministic",
             values=("vertical", "square", "landscape"),
             reliability="verified",
             basis=("Read from the video's own dimensions. Arithmetic, not "
                    "a model, so there is nothing to be wrong about.")),
)


# ---------------------------------------------------------------------------
# 3. Light and colour
#
# Deterministic measures are arithmetic on pixels and need no holdout;
# the VLM's reading of "lighting character" does.
# ---------------------------------------------------------------------------

LIGHT: tuple[Question, ...] = (
    Question("lighting", "What is the lighting like?", "enum", "vlm",
             reliability="indicative",
             values=("natural", "warm", "cool", "dramatic_low_key",
                     "flat_even", "backlit", "dark", "unknown"),
             basis="Extracted since M3, never checked against a label."),
    Question("brightness", "How bright is the opening?", "number",
             "deterministic", reliability="verified",
             basis="Mean luma over the sampled frames. Arithmetic."),
    Question("contrast", "How much contrast is there?", "number",
             "deterministic", reliability="verified",
             basis="Luma standard deviation over the sampled frames."),
    Question("colour_warmth", "Is the palette warm or cool?", "number",
             "deterministic", reliability="verified",
             basis="Mean red minus mean blue, normalised. Arithmetic."),
    Question("saturation", "How saturated are the colours?", "number",
             "deterministic", reliability="verified",
             basis="Mean HSV saturation over the sampled frames."),
    Question("edge_density", "How visually busy is the frame?", "number",
             "deterministic", reliability="verified",
             basis=("Proportion of pixels on a detected edge. Already in "
                    "analytical use and compared across the M6 cohort.")),
)


# ---------------------------------------------------------------------------
# 4. Where it is
# ---------------------------------------------------------------------------

PLACE: tuple[Question, ...] = (
    Question("setting", "Where does this appear to be?", "enum", "vlm",
             reliability="unverified",
             values=("concert_hall", "studio", "home_room", "outdoor",
                     "stage", "plain_backdrop", "church", "rehearsal_room",
                     "backstage", "unknown"),
             basis=("Measured and withheld in M3. The vocabulary has since "
                    "gained church, rehearsal_room and backstage, so the "
                    "old measurement does not describe this question any "
                    "more and it must be re-measured rather than inherited."
                    )),
    Question("formality", "How formal does it look?", "enum", "vlm",
             values=("concert_dress", "smart", "casual", "unknown"),
             interpretive=True),
)


# ---------------------------------------------------------------------------
# 5. Pace and structure
# ---------------------------------------------------------------------------

PACE: tuple[Question, ...] = (
    Question("scene_changes", "How many times does the shot change?",
             "count", "vlm", reliability="indicative",
             basis=("Extracted since M3 and never checked. A deterministic "
                    "cut detector would answer this better than a "
                    "captioning model and is the obvious next extractor.")),
    Question("cut_rate", "How fast is it cut?", "number", "deterministic",
             basis=("Not built. A frame-difference cut detector would make "
                    "this verifiable arithmetic rather than a model's "
                    "guess.")),
    Question("transitions", "What kind of transitions are used?",
             "enum", "vlm", reliability="indicative",
             values=("none", "hard_cut", "dissolve", "whip_pan", "zoom",
                     "unknown"),
             basis="Extracted since M3, never checked against a label."),
    Question("opening_type", "What do the first seconds actually do?",
             "enum", "vlm", reliability="indicative",
             values=("performance_immediately", "text_statement",
                     "talking_head", "slow_reveal", "close_detail",
                     "montage", "unknown"),
             basis=("Extracted since M3, never checked. Probably the single "
                    "most interesting question in this file for a creator, "
                    "and therefore the first that should go to a holdout.")),
    Question("opening_is_representative",
             "Does the opening look like the rest of the video?",
             "boolean", "vlm", reliability="indicative",
             basis="Extracted since M3, never checked against a label."),
    Question("starts_mid_phrase", "Does the music start already underway?",
             "boolean", "composite",
             basis="Not built. Needs the audio channel, not frames."),
)


# ---------------------------------------------------------------------------
# 6. Text on screen
# ---------------------------------------------------------------------------

TEXT: tuple[Question, ...] = (
    Question("text_overlay_present", "Is there text on screen?",
             "boolean", "vlm", reliability="verified",
             basis=("0.917 specificity over 80 held-out publications, "
                    "+0.138 lift. The strongest verified field, and the "
                    "one place the creator's added text can be told apart "
                    "from printed text already in the scene - OCR cannot "
                    "make that distinction."),
             needs_negatives=("openings with no added text at all. The own "
                              "cohort currently has exactly ONE, so this "
                              "field's own-account number is a coin toss "
                              "rather than a measurement.")),
    Question("text_word_count", "How many words are on screen?",
             "count", "ocr", reliability="verified",
             basis="Counted from recognised text. Arithmetic on the OCR."),
    Question("text_position", "Where on screen does the text sit?",
             "enum", "ocr",
             values=("top", "middle", "bottom", "scattered", "none")),
    Question("text_is_question", "Does the on-screen text ask a question?",
             "boolean", "ocr",
             basis="Not built. Trivial once OCR text is available."),
)


# ---------------------------------------------------------------------------
# 7. How the performer presents
#
# Interpretive by construction. These are readings, and a reading has
# no ground truth to be checked against, so none of them may ever
# reach `verified`. They can still be worth showing, clearly marked.
# ---------------------------------------------------------------------------

PRESENTATION: tuple[Question, ...] = (
    Question("emotional_presentation",
             "How does the opening present itself?", "text", "vlm",
             reliability="indicative", interpretive=True,
             basis=("A reading of the presentation, never of the person. "
                    "Carries a flag that forbids any consumer from "
                    "treating it as a measurement.")),
    Question("facing_camera", "Is the performer facing the camera?",
             "boolean", "vlm"),
    Question("performer_visible", "Is the performer in shot at all?",
             "boolean", "vlm", reliability="unverified",
             basis=("Measured and withheld in M3: the model answers yes "
                    "almost regardless of the frame, for the same reason "
                    "the piano question does.")),
)


#: Every specificity and lift quoted in a `basis` above is recomputed
#: from `tests/fixtures/vision/detector_comparison.json` by
#: `tests/server/test_vision_questions.py`. Three of them were wrong
#: when first written from memory, which is why the test exists.

ALL: tuple[Question, ...] = (
    IN_SHOT + CRAFT + LIGHT + PLACE + PACE + TEXT + PRESENTATION)

GROUPS: dict[str, tuple[Question, ...]] = {
    "What is in shot": IN_SHOT,
    "How it is shot": CRAFT,
    "Light and colour": LIGHT,
    "Where it is": PLACE,
    "Pace and structure": PACE,
    "Text on screen": TEXT,
    "How the performer presents": PRESENTATION,
}


def by_name(name: str) -> Question | None:
    return next((q for q in ALL if q.name == name), None)


def at(level: Reliability) -> tuple[Question, ...]:
    return tuple(q for q in ALL if q.reliability == level)


def may_enter_statistics(name: str) -> bool:
    """The single gate. Only a verified measurement may support a claim.

    `indicative` deliberately returns False. A reading shown to a
    person is not evidence, and the distinction is the entire point of
    the reliability grade.
    """
    q = by_name(name)
    return bool(q and q.reliability == "verified")


def may_be_shown(name: str) -> bool:
    """Whether a person may see it, clearly marked as a reading."""
    q = by_name(name)
    return bool(q and q.reliability in ("indicative", "verified"))


def holdout_requirements() -> dict[str, str]:
    """What the prospective holdout has to contain to judge anything.

    Phase 1 established that a corpus without deliberate negatives
    cannot measure specificity at all: a concert pianist's videos
    contain pianos and hands, so the M3 sample left 4 and 6 negatives
    for the piano question and 7 and 3 for hands. This is the list
    that stops the next collection pass repeating it.
    """
    return {q.name: q.needs_negatives for q in ALL if q.needs_negatives}


def buildable_today() -> tuple[Question, ...]:
    """Questions whose extractor exists, whatever their reliability."""
    return tuple(q for q in ALL if "Not built" not in q.basis)
