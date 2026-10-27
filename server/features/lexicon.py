"""Plain English for every measurement. One source, read by everything.

ViralLab is not an analytics dashboard. The science underneath has to
be rigorous; the surface has to be readable by someone who has never
heard of a rank correlation. This module is the join between the two,
and it is deliberately the *only* place a measurement is described in
words — a description written into a UI component is a description
that will contradict this one within a month.

## What this exists to prevent

The headline a reader should meet is

    Videos with more visual detail tend to have fewer viewers skipping
    them.

and never

    background_edge_density: rho = -0.401, partial rho = -0.318

The second is not wrong. It is unreadable, and unreadable numbers get
skipped, and skipped numbers get replaced by whatever the reader
already believed.

## Naming a thing for what it is, not what we wish it were

`text_region_score` is the case that matters most. It is a measure of
**local visual complexity in the regions where text usually sits**. It
is not a count of words, not a measure of caption length, and not
evidence that any text exists — `ocr_word_count` is the thing that
reads text. The proxy correlates with retention and the temptation to
call it "amount of text on screen" is exactly how a project starts
reporting a finding it did not make. `not_a_measure_of` exists for
this field first and everything else second.

## Direction is stated, never derived at display time

`reels_skip_rate` goes *down* when things go well. A correlation of
−0.40 between visual detail and skip rate is therefore good news, and
a layer that rendered "negative correlation" as "bad" would invert
every finding in the product. Each outcome carries `better_when` so
the direction is read from the data's own definition rather than from
the sign of a number.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Plain:
    """Everything a non-technical reader needs about one measurement."""

    #: What to call it on screen. Never the column name.
    display_name: str
    #: What it measures, in a sentence someone with no statistics can
    #: read.
    measures: str
    #: What a high value means, where that is meaningful.
    high_means: str = ""
    low_means: str = ""
    #: What it is NOT, stated because the name invites the wrong
    #: reading. The most important field in this dataclass.
    not_a_measure_of: str = ""
    #: How it is obtained, and how far it can be trusted.
    method: str = ""
    #: The caveat that travels with the number wherever it is shown.
    limitations: str = ""


@dataclass(frozen=True)
class OutcomePlain:
    """An outcome variable, and which way is good."""

    display_name: str
    measures: str
    #: "lower" or "higher" - which direction is the desirable one.
    better_when: str
    method: str = ""
    limitations: str = ""
    not_a_measure_of: str = ""


#: Outcomes. Separate from features because an outcome has a direction
#: and a feature does not.
OUTCOMES: dict[str, OutcomePlain] = {
    "reels_skip_rate": OutcomePlain(
        display_name="Skip rate",
        measures=("The share of viewers who skipped past the Reel "
                  "rather than watching on."),
        better_when="lower",
        method=("Reported by Instagram for Reels. Read once per "
                "publication at whatever age it happened to be when "
                "collected, not at a fixed age."),
        limitations=(
            "Instagram documents this as a share of initial views but "
            "never states whether the denominator is the organic view "
            "count. It may or may not include viewers who arrived "
            "through the paid promotion on this account, and nobody "
            "outside Meta can currently say which (ADR 0008). Treat "
            "the figure as directional rather than exact."),
        not_a_measure_of=(
            "how long anyone watched. It counts skips, not watch time, "
            "and the two are not interchangeable."),
    ),
    "text_region_score": OutcomePlain(
        display_name="Visual complexity in text areas",
        measures=("How visually busy the parts of the frame where "
                  "captions usually sit happen to be."),
        better_when="higher",
        method=("A deterministic image statistic. Used as an *outcome* "
                "in one arm of the visual-structure analysis, which is "
                "a diagnostic question about the measure itself, not a "
                "claim about audience behaviour."),
        not_a_measure_of=(
            "text. See the feature entry of the same name - this does "
            "not read, count or detect words."),
    ),
}


#: Features. Keyed by the name the database uses; the reader never
#: sees that key.
PLAIN: dict[str, Plain] = {
    # -- the nine that currently carry an association ----------------
    "edge_density": Plain(
        display_name="Overall visual detail",
        measures=("How much fine detail and how many distinct edges "
                  "fill the opening frames."),
        high_means="A busy, detailed or textured opening.",
        low_means="A clean, plain or softly-lit opening.",
        not_a_measure_of=(
            "quality, composition or interest. A cluttered frame and a "
            "richly detailed one score alike."),
        method=("Measured directly from the pixels of four frames "
                "across the first three seconds. Deterministic: the "
                "same video always gives the same number."),
    ),
    "background_edge_density": Plain(
        display_name="Background visual detail",
        measures=("How much detail sits in the parts of the frame that "
                  "are not moving - the room, the hall, the backdrop."),
        high_means="A busy or detailed setting behind the performer.",
        low_means="A plain, dark or uncluttered setting.",
        not_a_measure_of=(
            "whether the background is attractive, or whether anyone "
            "looked at it."),
        method=("The frame is split into moving and still regions, and "
                "detail is measured in the still part alone."),
        limitations=("Needs enough motion to separate subject from "
                     "background; a completely static shot cannot be "
                     "decomposed and is reported as unavailable."),
    ),
    "subject_edge_density": Plain(
        display_name="Subject visual detail",
        measures=("How much detail sits in the moving part of the "
                  "frame - usually the performer and their hands."),
        high_means="A detailed or textured subject.",
        low_means="A simple or smoothly-lit subject.",
        method="The complement of the background measurement above.",
    ),
    "background_grey_entropy": Plain(
        display_name="Background tonal variety",
        measures=("How many different shades of light and dark appear "
                  "in the still parts of the frame."),
        high_means="A background with varied tones.",
        low_means="A flat or evenly-lit background.",
        not_a_measure_of="colour. This looks at brightness only.",
    ),
    "background_subject_edge_ratio": Plain(
        display_name="Background detail relative to the subject",
        measures=("Whether the busiest part of the frame is the "
                  "performer or what is behind them."),
        high_means="The background competes with the performer.",
        low_means="The performer stands out against their setting.",
    ),
    "contrast_rms": Plain(
        display_name="Contrast",
        measures="How far the bright and dark parts of the frame sit apart.",
        high_means="A punchy, high-contrast image.",
        low_means="A flat or hazy image.",
    ),
    "brightness_mean": Plain(
        display_name="Brightness",
        measures="How light or dark the opening is overall.",
        high_means="A bright opening.",
        low_means="A dark or dimly-lit opening.",
    ),
    "saturation_mean": Plain(
        display_name="Colour intensity",
        measures="How strong and vivid the colours are.",
        high_means="Rich, saturated colour.",
        low_means="Muted, desaturated or near-monochrome.",
    ),
    "text_region_score": Plain(
        display_name="Visual complexity where captions usually sit",
        measures=("How visually busy the regions of the frame are "
                  "where on-screen text normally appears."),
        high_means=("Those regions are busy - which *may* mean text, "
                    "and may equally mean a detailed background."),
        low_means="Those regions are plain.",
        not_a_measure_of=(
            "text. It does not read, count or detect words, and a "
            "frame with no text at all can score highly. The feature "
            "that actually reads text is `ocr_word_count`, shown as "
            "'Words on screen'. Describing this as 'amount of text' "
            "would be reporting a finding nobody made."),
        method=("A deterministic image statistic, graded as a proxy "
                "rather than a measurement."),
        limitations=("Because it is a proxy, any association it has "
                     "with an outcome could belong to the text it "
                     "hints at, to the visual busyness it actually "
                     "measures, or to something correlated with both."),
    ),

    # -- the two promoted vision fields ------------------------------
    "vision_text_overlay_present": Plain(
        display_name="Caption text burned into the video",
        measures=("Whether the creator added words on top of the "
                  "video, as distinct from text already in the scene."),
        not_a_measure_of=(
            "what the text says, or how much of it there is."),
        method=("Read by a vision-language model. Checked twice "
                "against reference labels nobody had seen "
                "beforehand - 92% of openings with no caption were "
                "correctly identified as having none."),
    ),
    "vision_face_visible": Plain(
        display_name="Face visible in the opening",
        measures="Whether a human face appears in the first three seconds.",
        not_a_measure_of="whose face it is. People are never identified.",
        method=("Read by a vision-language model and checked against "
                "80 reference openings; 81% of those with no visible "
                "face were correctly identified."),
        limitations=(
            "Noticeably weaker on this account's own videos than on "
            "other creators': 75% correct on no-face openings here "
            "against 86% elsewhere, and 75% is below the bar the "
            "project set for trusting a field. Treat a face-related "
            "finding about your own videos as provisional."),
    ),

    # -- things a reader will ask about, which we cannot answer ------
    "vision_piano_or_keyboard_visible": Plain(
        display_name="Piano keys visible",
        measures="Whether piano keys appear in the opening.",
        method="Attempted with a vision-language model.",
        limitations=(
            "Not usable, and not shown as a finding. The model "
            "answered 'yes' to almost every opening including ones "
            "with no instrument at all - 1 of 10 keyless openings "
            "correctly identified. Three different approaches have "
            "failed on this and the field is withheld."),
        not_a_measure_of="anything, at present.",
    ),
    "vision_sheet_music_visible": Plain(
        display_name="Sheet music visible",
        measures="Whether printed notation appears in the opening.",
        method="Attempted with a vision-language model.",
        limitations=(
            "Withheld. A reworded instruction raises it from 19% to "
            "81% correct on openings with no notation, but adopting "
            "that wording costs accuracy on the two fields currently "
            "trusted, so it is not in use."),
    ),
    "ocr_word_count": Plain(
        display_name="Words on screen",
        measures=("How many words were actually read off the frames by "
                  "optical character recognition."),
        high_means="A text-heavy opening.",
        low_means="Few or no words on screen.",
        not_a_measure_of=(
            "whether the words were added by the creator or were "
            "already in the scene - sheet music and a shop sign both "
            "count. The field that distinguishes them is the caption "
            "overlay one above."),
        method="Optical character recognition over the opening frames.",
    ),
    "speech_present": Plain(
        display_name="Speech detected",
        measures="Whether anybody actually speaks in the opening.",
        method=("Gated behind a speech-presence check before any "
                "transcription is stored, because the transcriber "
                "invents words over music when left ungated."),
        limitations=("Only 1 to 2 publications in 14 in this niche "
                     "contain real speech, so this is usually false "
                     "and carries little information."),
    ),
    "cut_count": Plain(
        display_name="Cuts in the opening",
        measures="How many hard cuts happen in the first three seconds.",
        high_means="A fast, edited opening.",
        low_means="A single continuous shot.",
    ),
    "audio_starts_immediately": Plain(
        display_name="Sound from the first frame",
        measures="Whether audio is present at the very start.",
    ),
}


def display_name(feature: str) -> str:
    """What to call a feature on screen.

    Falls back to a readable form of the key rather than the key
    itself: a reader should never meet `background_grey_entropy`, and
    `Background grey entropy` is at least a phrase.
    """
    p = PLAIN.get(feature)
    if p:
        return p.display_name
    base = feature.removeprefix("vision_").removeprefix("audio_")
    return base.replace("_", " ").strip().capitalize()


def outcome_name(outcome: str) -> str:
    o = OUTCOMES.get(outcome)
    return o.display_name if o else outcome.replace("_", " ").capitalize()


def better_when(outcome: str) -> str:
    """Which direction of an outcome is the good one.

    Defaults to "higher" and says so in the catalogue rather than
    guessing silently: an unknown direction is a reason to withhold an
    interpretation, not to assume the optimistic one.
    """
    o = OUTCOMES.get(outcome)
    return o.better_when if o else "unknown"


def as_dict(feature: str) -> dict:
    """The plain-English record for one feature, or a minimal stub."""
    p = PLAIN.get(feature)
    if p is None:
        return {"display_name": display_name(feature), "measures": "",
                "high_means": "", "low_means": "", "not_a_measure_of": "",
                "method": "", "limitations": "", "written": False}
    return {
        "display_name": p.display_name,
        "measures": p.measures,
        "high_means": p.high_means,
        "low_means": p.low_means,
        "not_a_measure_of": p.not_a_measure_of,
        "method": p.method,
        "limitations": p.limitations,
        "written": True,
    }


def outcome_as_dict(outcome: str) -> dict:
    o = OUTCOMES.get(outcome)
    if o is None:
        return {"display_name": outcome_name(outcome), "measures": "",
                "better_when": "unknown", "method": "", "limitations": "",
                "not_a_measure_of": "", "written": False}
    return {
        "display_name": o.display_name,
        "measures": o.measures,
        "better_when": o.better_when,
        "method": o.method,
        "limitations": o.limitations,
        "not_a_measure_of": o.not_a_measure_of,
        "written": True,
    }
