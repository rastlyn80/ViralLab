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
            "frame with no text at all can score highly. The measurement "
            "that actually reads text is the one shown as 'Words on "
            "screen'. Describing this as 'amount of text' would be "
            "reporting a finding nobody made."),
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

    # -- audio -------------------------------------------------------
    "audio_onset_count": Plain(
        display_name="Note attacks in the opening",
        measures=("How many distinct note or beat attacks happen in the "
                  "first three seconds."),
        high_means="A busy, fast or rhythmically dense opening.",
        low_means="A sparse or sustained opening.",
        not_a_measure_of="tempo. A slow piece played in thick chords "
                         "can have more attacks than a fast single line.",
    ),
    "audio_rms_mean": Plain(
        display_name="Average loudness",
        measures="How loud the opening is on average.",
        high_means="A loud or close-miked opening.",
        low_means="A quiet or distant one.",
        not_a_measure_of="how loud it sounds to a listener, which "
                         "depends on the platform's own normalisation.",
    ),
    "audio_rms_peak": Plain(
        display_name="Loudest moment",
        measures="The loudest instant in the opening.",
    ),
    "audio_attack_time_s": Plain(
        display_name="Time to the loudest point",
        measures="How quickly the sound reaches its peak.",
        high_means="A slow swell.",
        low_means="An immediate hit.",
    ),
    "first_sound_at_s": Plain(
        display_name="When sound starts",
        measures="How many seconds pass before anything is audible.",
        low_means="Sound from the first frame.",
    ),
    "audio_novelty_vs_later": Plain(
        display_name="How different the opening sounds",
        measures=("How much the sound of the first seconds differs from "
                  "a later part of the same video."),
        high_means="The opening does not represent the rest.",
        low_means="The opening sounds like the whole.",
    ),
    "spectral_centroid_hz": Plain(
        display_name="Brightness of the sound",
        measures=("Where the weight of the sound sits in pitch - a "
                  "bright, trebly sound scores high and a dark, bassy "
                  "one low."),
        not_a_measure_of="how loud it is, or what notes are played.",
    ),
    "spectral_flatness_mean": Plain(
        display_name="How noise-like the sound is",
        measures=("Whether the sound is tonal, like a held piano note, "
                  "or noisy, like applause or room hiss."),
        high_means="Noisy or percussive.",
        low_means="Clear and tonal.",
    ),
    "voice_band_ratio": Plain(
        display_name="Energy in the speaking range",
        measures=("How much of the sound sits in the frequency band "
                  "where human speech normally lives."),
        not_a_measure_of=("speech. Piano in the middle register sits in "
                          "the same band; the field that actually "
                          "detects speech is shown as 'Speech "
                          "detected'."),
    ),
    "audio_tempo_bpm": Plain(
        display_name="Tempo",
        measures="Beats per minute in the opening.",
        limitations="Not built. Nothing measures this yet.",
    ),
    "audio_climax_at_s": Plain(
        display_name="When the music peaks",
        measures="When the loudest or densest moment arrives.",
        limitations="Not built. Nothing measures this yet.",
    ),

    # -- visual ------------------------------------------------------
    "background_static_fraction": Plain(
        display_name="How much of the frame holds still",
        measures=("The share of the picture that does not move between "
                  "frames - the wall, the room, a fixed instrument."),
        high_means="A locked-off shot with a still background.",
        low_means="Lots of movement or a moving camera.",
    ),
    "mean_abs_frame_diff": Plain(
        display_name="Frame-to-frame change",
        measures="How much the picture changes from one frame to the next.",
        high_means="Fast movement or rapid cutting.",
        low_means="A near-still image.",
    ),
    "visual_novelty_vs_later": Plain(
        display_name="How different the opening looks",
        measures=("How much the first seconds differ visually from a "
                  "later part of the same video."),
        high_means="The opening does not represent the rest.",
    ),
    "cut_times_s": Plain(
        display_name="When each cut happens",
        measures="The moment of every hard cut in the opening, in seconds.",
    ),
    "scene_change_count": Plain(
        display_name="Scene changes",
        measures=("How many times the picture changes enough to count "
                  "as a new scene."),
        not_a_measure_of=("cuts. A slow dissolve or a camera turning to "
                          "a new subject can register without any cut."),
    ),
    "camera_motion_class": Plain(
        display_name="Camera movement",
        measures="Whether the camera is static, panning or handheld.",
    ),
    "camera_motion_px_per_frame": Plain(
        display_name="How much the camera moves",
        measures="Estimated camera movement, in pixels per frame.",
    ),
    "camera_motion_source_px": Plain(
        display_name="Scale the camera movement was measured at",
        measures=("The pixel width the motion estimate was computed on. "
                  "A bookkeeping figure, not a property of the video."),
    ),
    "text_present_estimate": Plain(
        display_name="Probably has on-screen text",
        measures=("Whether the visual-complexity proxy crosses the "
                  "threshold where text usually sits."),
        not_a_measure_of=("text. It is a guess from busyness; 'Words on "
                          "screen' is the measurement that reads it."),
    ),

    # -- on-screen text ----------------------------------------------
    "ocr_text_present": Plain(
        display_name="Any text read on screen",
        measures="Whether optical character recognition read any words.",
        not_a_measure_of=("whether the creator added it. Sheet music, a "
                          "poster and a burned-in caption all count."),
    ),
    "ocr_text": Plain(
        display_name="The text read on screen",
        measures="The words recognition actually read off the frames.",
        limitations="Recognition errors are kept as read, not corrected.",
    ),
    "ocr_element_count": Plain(
        display_name="Separate blocks of text",
        measures="How many distinct pieces of text appear.",
    ),
    "ocr_elements": Plain(
        display_name="Each block of text and where it sits",
        measures="Every text element with its position in the frame.",
    ),
    "ocr_first_text_at_s": Plain(
        display_name="When text first appears",
        measures="How many seconds before any text is on screen.",
    ),
    "ocr_text_duration_s": Plain(
        display_name="How long text stays up",
        measures="Total seconds of the opening with text visible.",
    ),
    "ocr_text_changes": Plain(
        display_name="How often the text changes",
        measures="How many times the on-screen text is replaced.",
        high_means="Fast-changing captions.",
    ),
    "ocr_max_text_height_frac": Plain(
        display_name="Size of the largest text",
        measures=("The height of the biggest text as a share of the "
                  "frame - 0.2 means a fifth of the screen tall."),
    ),
    "ocr_mean_confidence": Plain(
        display_name="How sure the text reader was",
        measures="The recognition engine's own confidence.",
        not_a_measure_of=("whether it was right. It is uncalibrated and "
                          "a confident misread scores highly."),
    ),
    "ocr_text_reading_order": Plain(
        display_name="The order the text was read in",
        measures="A diagnostic showing how blocks were sequenced.",
    ),

    # -- speech ------------------------------------------------------
    "spoken_words": Plain(
        display_name="Words spoken",
        measures="Words transcribed, where real speech was detected.",
        not_a_measure_of=("singing or lyrics. It also stays empty on "
                          "music-only video by design."),
        limitations=("Stored only when a speech check passes first. "
                     "Ungated, the transcriber invents sentences over "
                     "music - 'Thank you.' and 'Music' repeatedly."),
    ),
    "speech_seconds": Plain(
        display_name="Seconds containing speech",
        measures="How much of the opening has somebody talking.",
    ),
    "first_speech_at_s": Plain(
        display_name="When speech starts",
        measures="How many seconds before anybody speaks.",
    ),
    "speech_present_estimate": Plain(
        display_name="Probably has speech",
        measures=("A cheap guess from the audio frequency band alone, "
                  "before any transcription is attempted."),
        not_a_measure_of=("speech. Mid-register piano lives in the same "
                          "band. 'Speech detected' is the real answer."),
    ),
    "rejected_transcript": Plain(
        display_name="A transcript we threw away",
        measures=("Text the transcriber produced that the speech check "
                  "refused, kept so a hallucination can be inspected "
                  "rather than silently stored as fact."),
        not_a_measure_of="anything the video contains.",
    ),

    # -- the model's scene reading -----------------------------------
    "vision_performer_visible": Plain(
        display_name="A performer is in shot",
        measures="Whether any part of a performing person is visible.",
        limitations=("Withheld. There are only five openings in the "
                     "whole reference set with nobody in them, so there "
                     "is almost nothing to test the answer against."),
    ),
    "vision_hands_visible": Plain(
        display_name="Hands in shot",
        measures="Whether hands are visible in the opening.",
        limitations=("Withheld. The model says yes to nearly everything "
                     "- 1 of 10 hand-free openings identified."),
    ),
    "vision_shot_distance": Plain(
        display_name="How close the shot is",
        measures="Close, medium or wide.",
        limitations=("Withheld. It answers 'medium' for most openings "
                     "and scores below simply guessing the commonest."),
    ),
    "vision_setting": Plain(
        display_name="Where it was filmed",
        measures="Concert hall, studio, home room or outdoors.",
        limitations=("Withheld. Scores well below guessing the "
                     "commonest setting every time."),
    ),
    "vision_camera_angle": Plain(
        display_name="Camera angle",
        measures="The dominant angle - front, side, overhead, behind.",
        limitations="Withheld; never checked against reference labels.",
    ),
    "vision_framing": Plain(
        display_name="How the subject is framed",
        measures="Centred, off-centre, split, filling the frame.",
        limitations="Withheld; never checked against reference labels.",
    ),
    "vision_lighting": Plain(
        display_name="Lighting",
        measures="Natural, warm, cool, dramatic, flat, backlit or dark.",
        limitations="Withheld; never checked against reference labels.",
    ),
    "vision_opening_type": Plain(
        display_name="What the opening does",
        measures=("Whether it starts playing immediately, shows a title, "
                  "talks to camera, reveals slowly, or cuts about."),
        limitations="Withheld; never checked against reference labels.",
    ),
    "vision_opening_is_representative": Plain(
        display_name="The opening resembles the rest",
        measures="Whether the hook looks like the remainder of the video.",
        limitations="Withheld; never checked against reference labels.",
    ),
    "vision_transitions": Plain(
        display_name="Transitions the model saw",
        measures="Cuts, dissolves, whip pans and zooms, in order.",
        limitations="Withheld; never checked against reference labels.",
    ),
    "vision_scene_changes": Plain(
        display_name="Scene changes the model counted",
        measures="How many scene changes the model reports.",
        not_a_measure_of=("the deterministic count of the same name, "
                          "which is measured from pixels rather than "
                          "reported by a model."),
        limitations="Withheld; never checked against reference labels.",
    ),
    "vision_emotional_presentation": Plain(
        display_name="How the opening presents itself",
        measures=("The model's reading of the mood an opening projects - "
                  "intimate, dramatic, casual."),
        not_a_measure_of=("what the performer feels, or what a viewer "
                          "feels. It is an interpretation of the "
                          "presentation and is flagged as one in the "
                          "data so nothing can read it as measured."),
        limitations="Withheld. An interpretation, never a measurement.",
    ),
    "vision_confidence": Plain(
        display_name="How sure the model said it was",
        measures="A number the model assigns to its own answer.",
        not_a_measure_of=("how often it is right. It is uncalibrated, "
                          "and the benchmark found it confident about "
                          "answers that were wrong."),
    ),
    "vision_uncertain_fields": Plain(
        display_name="Fields the model flagged as uncertain",
        measures="Which parts of its own answer the model doubted.",
    ),
    "vision_frames_described": Plain(
        display_name="Frames the answer claims to use",
        measures=("How many frames the model says it looked at. An "
                  "input check: a model claiming six when four were "
                  "sent has not read the input."),
        not_a_measure_of="the video.",
    ),
    "vision_raw": Plain(
        display_name="The model's full answer",
        measures="Everything the model returned, stored verbatim.",
    ),

    # -- placeholders superseded by the model ------------------------
    "performer_visible": Plain(
        display_name="A performer is in shot (not measured here)",
        measures=("Recorded as 'cannot say' by the pixel-only pass, "
                  "which has no way to recognise a person."),
        not_a_measure_of="anything. It is a recorded absence of an answer.",
        limitations=("Kept rather than omitted so that asking the "
                     "question returns 'we cannot tell you' instead of "
                     "a missing value something might read as 'no'."),
    ),
    "piano_visible": Plain(
        display_name="A piano is in shot (not measured here)",
        measures="Recorded as 'cannot say' by the pixel-only pass.",
        not_a_measure_of="anything. It is a recorded absence of an answer.",
    ),
    "hands_visible": Plain(
        display_name="Hands in shot (not measured here)",
        measures="Recorded as 'cannot say' by the pixel-only pass.",
        not_a_measure_of="anything. It is a recorded absence of an answer.",
    ),
    "face_visible": Plain(
        display_name="A face is in shot (not measured here)",
        measures="Recorded as 'cannot say' by the pixel-only pass.",
        not_a_measure_of="anything. It is a recorded absence of an answer.",
    ),
    "framing_class": Plain(
        display_name="Shot framing (not measured here)",
        measures="Recorded as 'cannot say' by the pixel-only pass.",
        not_a_measure_of="anything. It is a recorded absence of an answer.",
    ),
    "emotional_presentation": Plain(
        display_name="Mood of the opening (not measured here)",
        measures="Recorded as 'cannot say' by the pixel-only pass.",
        not_a_measure_of="anything. It is a recorded absence of an answer.",
    ),
    "on_screen_text_content": Plain(
        display_name="What the on-screen text says (not measured here)",
        measures=("Recorded as 'cannot say' by the pixel-only pass, "
                  "which can suspect text exists but cannot read it."),
        not_a_measure_of="anything. 'The text read on screen' is the "
                         "field that answers this.",
    ),

    # -- caption and repertoire, not built ---------------------------
    "caption_length_chars": Plain(
        display_name="Caption length",
        measures="How many characters the caption runs to.",
        limitations="Not built. Nothing measures this yet.",
    ),
    "caption_intent_class": Plain(
        display_name="What the caption is doing",
        measures=("Whether the caption asks a question, states "
                  "something, or asks the viewer to act."),
        limitations="Not built. Nothing measures this yet.",
    ),
    "repertoire_work": Plain(
        display_name="The piece being played",
        measures=("Which work a publication contains, from the owner's "
                  "own performance archive."),
        not_a_measure_of=("anything read from the video. Repertoire is "
                          "never guessed from frames; a caption mention "
                          "is a mention, not an identification."),
        limitations=("Not available. 25 publications trace to a "
                     "performance, but the work field is empty on all "
                     "146 performances, so these group by sitting at "
                     "the piano rather than by piece."),
    ),
    "repertoire_composer": Plain(
        display_name="The composer",
        measures="Which composer, on the same terms as the piece.",
        limitations="Not available, as for the piece.",
    ),

    # -- timing, caption and repertoire, measured ---------------------
    # Added when the deeper competitor corpus started surfacing
    # findings on features that had never reached a page: the UI
    # audit caught "hashtag_count" rendered as a column name. The
    # fallback (capitalise the key) is not good enough - "Posted
    # hour utc" and "Hook is pov" are both jargon wearing a
    # sentence's clothes.
    "posted_hour_utc": Plain(
        display_name="Hour of day it went out",
        measures="The hour the publication went live, in UTC.",
        not_a_measure_of=("the hour in your audience's time zone, or "
                          "in yours. UTC is the storage format, not a "
                          "claim about when anyone was awake."),
        limitations=("An audience spread across time zones makes a "
                     "single posting hour close to meaningless. Read "
                     "it as a description of your habit, not of their "
                     "attention."),
    ),
    "posted_weekday": Plain(
        display_name="Day of the week",
        measures="Which day it went out, Monday as 0.",
        limitations=("A number, so an average over it is nonsense - "
                     "Wednesday is not the mean of Sunday and "
                     "Saturday. Compare days, never average them."),
    ),
    "posted_is_weekend": Plain(
        display_name="Posted at the weekend",
        measures="Whether it went out on a Saturday or Sunday, UTC.",
    ),
    "days_since_previous_post": Plain(
        display_name="Days since your previous post",
        measures="The gap between this publication and the one before.",
        not_a_measure_of=("how often you post in general. This is one "
                          "gap, not a rate."),
    ),
    "usual_gap_days": Plain(
        display_name="Your usual gap between posts",
        measures=("The typical number of days between publications, "
                  "at the moment this one went out."),
        method=("Computed from earlier publications only. Using later "
                "ones would let the future describe the past."),
    ),
    "gap_vs_usual": Plain(
        display_name="This gap against your usual one",
        measures=("How this gap compares with the creator's own habit "
                  "- above 1 means a longer silence than usual."),
        high_means="a longer pause than this creator normally leaves.",
        low_means="posted sooner after the last one than usual.",
    ),
    "publications_before_this": Plain(
        display_name="How many you had already posted",
        measures="The size of the catalogue when this one went out.",
        not_a_measure_of=("anything about the video. It is a count of "
                          "elapsed career, which means it tracks time "
                          "and audience growth together and cannot be "
                          "acted on when making the next one."),
        limitations=("Treated as a time proxy and reported apart from "
                     "content findings for that reason."),
    ),
    "is_first_post": Plain(
        display_name="The first thing on the account",
        measures="Whether this is the creator's earliest publication.",
    ),
    "caption_chars": Plain(
        display_name="Caption length",
        measures="How many characters the caption runs to.",
    ),
    "caption_words": Plain(
        display_name="Words in the caption",
        measures="How many words the caption runs to.",
    ),
    "caption_first_line_words": Plain(
        display_name="Words before the More link",
        measures=("How many words appear before the caption is cut "
                  "off, which is all most viewers read."),
        limitations=("Where the cut actually falls depends on the "
                     "app, the device and the font. A word count is a "
                     "stand-in for it, not the thing itself."),
    ),
    "caption_emoji_count": Plain(
        display_name="Emoji in the caption",
        measures="How many emoji the caption contains.",
    ),
    "caption_has_question": Plain(
        display_name="The caption asks something",
        measures="Whether the caption puts a question to the reader.",
        method=("Matched on question marks and opening question "
                "words. A rhetorical question counts, because the "
                "pattern cannot tell the difference."),
    ),
    "caption_has_cta": Plain(
        display_name="The caption asks you to do something",
        measures=("Whether it asks the reader to follow, comment, "
                  "save, share or tag."),
        method="Matched against a written-down phrase list.",
    ),
    "caption_is_hashtags_only": Plain(
        display_name="Caption is only hashtags",
        measures="Whether the caption carries no sentence at all.",
    ),
    "hashtag_count": Plain(
        display_name="Number of hashtags",
        measures="How many hashtags the caption carries.",
        not_a_measure_of=("whether they are the right hashtags, or "
                          "whether anyone browses them. It counts "
                          "them and nothing else."),
    ),
    "mention_count": Plain(
        display_name="Accounts tagged",
        measures="How many other accounts the caption mentions.",
    ),
    "duration_s": Plain(
        display_name="How long the video runs",
        measures="The length of the publication in seconds.",
    ),

    # -- what is being played -----------------------------------------
    "composer": Plain(
        display_name="Composer",
        measures="Which composer the publication credits.",
        method=("Read from the caption against a name list. A "
                "publication that names nobody has no value here, "
                "which is not the same as having no composer."),
    ),
    "composer_named": Plain(
        display_name="A composer is named",
        measures="Whether the caption names a composer at all.",
    ),
    "composer_period": Plain(
        display_name="Period",
        measures=("Baroque, Classical, Romantic, Modern or "
                  "Contemporary, from the composer."),
        limitations=("A composer's period, not a piece's. A late "
                     "Beethoven work and an early one are filed "
                     "together."),
    ),
    "composer_is_mainstream": Plain(
        display_name="A composer most people have heard of",
        measures=("Whether the composer is one a general audience "
                  "would recognise by name."),
        not_a_measure_of=("musical worth, and not a judgement about "
                          "the composer. It is a statement about an "
                          "audience outside classical music."),
        method="A written-down list, which can be argued with.",
    ),
    "piece_recognisability": Plain(
        display_name="How recognisable the piece is",
        measures=("Whether a non-specialist would know the work "
                  "within a few seconds: tier 1 is instantly "
                  "familiar, tier 2 broadly known, tier 0 neither."),
        not_a_measure_of=("quality or difficulty. The hardest piece "
                          "in the repertoire can be unrecognisable "
                          "and a simple one instantly known."),
        method=("Two written-down lists. The owner's own view of "
                "which pieces land is better than any model's and "
                "this is where it goes."),
    ),
    "piece_is_widely_known": Plain(
        display_name="A piece most people would recognise",
        measures="Whether the work is in the instantly-familiar tier.",
    ),
    "work_form_named": Plain(
        display_name="The form is named",
        measures=("Whether the caption says what kind of piece it is "
                  "- nocturne, etude, sonata."),
    ),
    "content_origin": Plain(
        display_name="Whose music it is",
        measures=("Whether the publication is repertoire, the "
                  "owner's own composition, an arrangement or an "
                  "improvisation."),
        limitations=("Read from the caption. A composition posted "
                     "without saying so is filed as repertoire, and "
                     "nothing in the video can correct that."),
    ),
    "is_own_composition": Plain(
        display_name="Your own composition",
        measures="Whether the music is the creator's own.",
        limitations=("Worth separating because it behaves "
                     "differently: the owner's own compositions take "
                     "a median 746 views against repertoire's 3,211, "
                     "so analysing them together mostly describes "
                     "the repertoire half."),
    ),
    "is_arrangement": Plain(
        display_name="An arrangement",
        measures=("Whether it is someone else's music rewritten - a "
                  "transcription, a cover, a mashup."),
    ),
    "is_improvisation": Plain(
        display_name="Improvised",
        measures="Whether the caption says it was made up on the spot.",
    ),
    "composer_repeated_recently": Plain(
        display_name="Same composer as recently",
        measures=("Whether this composer also appeared in the "
                  "creator's recent publications."),
        method="Looks backwards only, never at later publications.",
    ),
    "distinct_composers_recently": Plain(
        display_name="How varied the recent repertoire was",
        measures=("How many different composers appeared in the "
                  "recent run before this one."),
    ),
    "share_recent_same_composer": Plain(
        display_name="How much recent work was this composer",
        measures=("The share of the recent run given to the same "
                  "composer as this publication."),
        high_means="a run concentrated on one composer.",
    ),
    "form_repeated_recently": Plain(
        display_name="Same kind of piece as recently",
        measures=("Whether the recent run also named the form of "
                  "the work."),
    ),

    # -- what the opening text is doing -------------------------------
    # Not how much text, which was already measured - what kind of
    # sentence it is, which is the part a creator can act on.
    "hook_is_pov": Plain(
        display_name="Puts the viewer in the scene",
        measures=("Whether the opening text addresses the viewer as "
                  "if they were inside it - \"POV:\", \"when you\", "
                  "\"imagine\"."),
        method=("Matched against a written-down phrase list, which "
                "is blunt, free, and can be corrected by reading it."),
    ),
    "hook_is_question": Plain(
        display_name="Asks the viewer something",
        measures="Whether the opening text puts a question to them.",
    ),
    "hook_is_what_if": Plain(
        display_name="Poses a what-if",
        measures=("Whether it sets up a counterfactual - \"what if "
                  "Rachmaninoff wrote We Are The Champions\"."),
    ),
    "hook_is_story": Plain(
        display_name="Tells a story",
        measures=("Whether the opening text narrates something "
                  "rather than labelling the content."),
    ),
    "hook_has_cta": Plain(
        display_name="Asks the viewer to do something",
        measures="Whether it asks them to tag, comment, follow or save.",
    ),
    "hook_is_instruction": Plain(
        display_name="Tells the viewer how to listen",
        measures=("Whether it directs attention - \"close your "
                  "eyes\", \"wait for\", \"headphones on\"."),
    ),
    "hook_has_number_claim": Plain(
        display_name="A number carries the hook",
        measures=("Whether a figure does the work - \"40 years of "
                  "practice\", \"in 2 seconds\"."),
    ),
    "hook_names_celebrity": Plain(
        display_name="Names someone outside classical music",
        measures=("Whether the opening text borrows a name a general "
                  "audience knows."),
        method=("A deliberately short list. A long one would catch "
                "composers and turn every repertoire caption into "
                "a celebrity hook."),
    ),
    "hook_has_contrast": Plain(
        display_name="Sets up a reversal",
        measures=("Whether it turns on a contrast - \"but\", "
                  "\"until\", \"turns out\"."),
    ),
    "hook_is_plain_label": Plain(
        display_name="Just names the piece",
        measures=("Whether the opening text uses no device at all - "
                  "\"Chopin Nocturne op. 9 no. 2\"."),
        not_a_measure_of=("a mistake. Naming the piece is a choice, "
                          "and for a recognisable work it may be the "
                          "right one."),
    ),
    "hook_device_count": Plain(
        display_name="How many devices the opening stacks",
        measures=("How many of the above the opening text uses at "
                  "once. Stacking them is itself a choice."),
    ),
    "hook_text_length": Plain(
        display_name="Characters read from the screen",
        measures="How much text was read off the opening frames.",
        not_a_measure_of=("how much text a viewer sees. Watermarks "
                          "and repeated frame reads inflate it."),
    ),

    # -- what is in the room ------------------------------------------
    "vision_second_person_visible": Plain(
        display_name="Someone else in frame",
        measures="Whether a second person appears.",
        not_a_measure_of=("who they are. The model will not identify "
                          "people and is not asked to."),
    ),
    "vision_audience_visible": Plain(
        display_name="An audience is visible",
        measures="Whether listeners appear in the frame.",
    ),
    "vision_facing_camera": Plain(
        display_name="The performer faces the camera",
        measures="Whether the player is turned towards the lens.",
    ),
    "vision_other_instrument_visible": Plain(
        display_name="Another instrument is visible",
        measures="Whether an instrument other than the piano appears.",
    ),
    "vision_microphone_visible": Plain(
        display_name="A microphone is visible",
        measures="Whether a mic appears in shot.",
    ),
    "vision_recording_gear_visible": Plain(
        display_name="Recording equipment is visible",
        measures="Whether cameras, stands or cables appear.",
    ),
    "vision_music_stand_visible": Plain(
        display_name="A music stand is visible",
        measures="Whether a stand or propped score appears.",
    ),
    "vision_screen_visible": Plain(
        display_name="A screen is visible",
        measures="Whether a phone, tablet or monitor appears.",
    ),
    "vision_window_visible": Plain(
        display_name="A window is visible",
        measures="Whether a window appears in the frame.",
    ),
    "vision_chandelier_visible": Plain(
        display_name="A chandelier is visible",
        measures="Whether a chandelier or grand light fitting appears.",
    ),
    "vision_candles_or_flowers_visible": Plain(
        display_name="Candles or flowers are visible",
        measures="Whether either is dressed into the shot.",
    ),
    "vision_pet_or_child_visible": Plain(
        display_name="A pet or child is visible",
        measures="Whether an animal or a child appears.",
    ),
    "vision_formality": Plain(
        display_name="How formal the setting looks",
        measures=("Whether the surroundings read as a concert hall, "
                  "a studio or a living room."),
        not_a_measure_of=("how good the performance is, or how "
                          "seriously it should be taken."),
    ),
    "vision_camera_movement": Plain(
        display_name="Whether the camera moves",
        measures="Whether the shot is locked off, panning or handheld.",
        limitations=("Judged from four still frames, so slow movement "
                     "and a static shot can look alike."),
    ),
    "vision_depth_of_field": Plain(
        display_name="How blurred the background is",
        measures="Whether the background falls out of focus.",
    ),
    "vision_frame_descriptions": Plain(
        display_name="What the model saw, in its own words",
        measures=("The written description each sampled frame "
                  "produced, kept so an answer can be checked "
                  "against what was actually in front of it."),
        not_a_measure_of=("a number. It is evidence for the other "
                          "answers, not a measurement itself."),
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


# ---------------------------------------------------------------------------
# Turning stored keys inside prose into names a reader knows
# ---------------------------------------------------------------------------

#: Statistical vocabulary, translated where it appears in a sentence
#: meant for a creator. The exact terms stay available in the
#: technical disclosure; what this prevents is a verdict line reading
#: "Spearman rho against reels_skip_rate" on the main surface.
STATS_PHRASES: tuple[tuple[str, str], ...] = (
    ("Spearman rank correlation", "a measure of how closely two things "
                                  "move together"),
    ("Spearman", "rank-correlation"),
    ("Benjamini-Hochberg", "a correction for testing many things at once"),
    ("Benjamini\u2013Hochberg",
     "a correction for testing many things at once"),
    ("false discovery rate", "the chance of a finding being a fluke"),
    ("partial correlation", "the relationship after allowing for other "
                            "differences between videos"),
)

#: Keys that must never be swapped inside prose, because the sentence
#: is about the identifier itself.
_NEVER_SWAP: frozenset[str] = frozenset()


def humanise(text: str | None) -> str | None:
    """Rewrite a sentence so a non-specialist can read it.

    Server-side prose - a verdict, a caveat, an outcome note - is
    written by analysts and names columns. Rather than rewriting every
    such string by hand and watching them drift back, this swaps any
    stored key for its display name and softens the statistical
    vocabulary.

    **Longest key first.** `background_edge_density` contains
    `edge_density`; replacing the shorter one first would leave
    "background Overall visual detail", which is worse than the key
    it replaced.

    Returns the input unchanged when there is nothing to do, so a
    caller can apply it indiscriminately.
    """
    if not text:
        return text
    out = text
    for key in sorted(PLAIN, key=len, reverse=True):
        if key in _NEVER_SWAP or key not in out:
            continue
        out = out.replace(f"`{key}`", PLAIN[key].display_name)
        out = out.replace(key, PLAIN[key].display_name)
    for key in sorted(OUTCOMES, key=len, reverse=True):
        if key not in out:
            continue
        out = out.replace(f"`{key}`", OUTCOMES[key].display_name)
        out = out.replace(key, OUTCOMES[key].display_name)
    for term, plain_term in STATS_PHRASES:
        out = out.replace(term, plain_term)
    return out


def humanise_all(items) -> list:
    """`humanise` over a list of strings, preserving order."""
    return [humanise(x) for x in (items or [])]
