"""What the text on screen actually says, as a shape.

`ocr_text` holds the words. Nothing has ever read them. The project
measured how MUCH text there was, how big, where, and when it
appeared, and never once what kind of sentence it was - which is the
only part a creator can act on when writing the next one.

Reading the owner's own breakthrough videos makes the shapes
obvious:

    POV: you are a concert pianist and you've reached this passage.
    What if Rachmaninoff wrote We Are The Champions?
    Which emotions does this make you feel?
    This piano was terrible. Out of tune. Missing keys.
    When you accidentally become a concert pianist because your dad
    bought a piano...

Those are five different devices, not five amounts of text. A hook
that addresses the viewer as the performer is doing something a
hook that asks them a question is not.

## Patterns, not a model

Matched against written-down phrase lists. A language model could
classify these more subtly and would make this feature unverifiable
without a labelled set, which the project does not have and cannot
cheaply get. A pattern list is blunt, auditable, free, and can be
corrected by the owner reading it - which is what the project's
whole evidence ladder is for.

A hook can carry more than one device, so each is a boolean and
they do not sum to one. `hook_device_count` records how many,
because stacking devices is itself a choice.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("virallab.features.hooktext")

METHOD_VERSION = "hooktext-v1"

#: Addressing the viewer as if they were inside the scene. The
#: single most common device in this owner's breakthrough videos.
POV = (
    "pov:", "pov ", "p.o.v", "when you ", "when your ", "that moment when",
    "imagine ", "you are a ", "you're a ", "youare",
)

#: Asking the viewer something. Checked for a question mark too, but
#: separately: plenty of captions end in "?" without asking anything
#: of the viewer.
QUESTION_WORDS = (
    "which ", "what ", "how ", "why ", "who ", "can you ", "do you ",
    "would you ", "guess ", "name the", "one word",
)

#: A counterfactual. "What if Rachmaninoff wrote We Are The
#: Champions" is a different promise from "here is Rachmaninoff".
WHAT_IF = ("what if", "imagine if", "but make it", "in the style of")

#: Telling a story rather than labelling the content.
STORY = (
    " i ", "i was", "i spent", "i turned", "she ", "he ", "they ",
    "my dad", "my mother", "my teacher", "years ago", "that day",
    "had no idea", "it was", "nobody ", "no one ",
    # Past-tense narration. "This piano WAS terrible, out of tune,
    # missing keys" is a story and the first draft filed it as a
    # plain label, because every pattern above needed a pronoun.
    " was ", " were ", " had ", "used to ", "never ", "finally ",
)

#: Asking the viewer to do something.
CTA = (
    "tag a", "tag someone", "comment", "follow", "save this", "share",
    "link in bio", "let me know", "tell me",
)

#: Telling the viewer how to listen.
INSTRUCTION = (
    "close your eyes", "listen to", "wait for", "watch till", "turn up",
    "headphones", "stay here", "keep watching",
)

#: A number used as the hook - "40 years of practice", "2 seconds".
NUMBER_CLAIM = re.compile(
    r"\b\d+\s*(years?|hours?|minutes?|seconds?|days?|weeks?|months?|"
    r"times?|notes?|bars?)\b", re.I)

#: Naming a person the audience knows, which borrows their audience.
#: Deliberately short and specific: a long list would catch composers
#: and turn every repertoire caption into a celebrity hook.
CELEBRITY = (
    "taylor swift", "jude law", "billie eilish", "beyonce", "adele",
    "ed sheeran", "lady gaga", "hans zimmer", "ludovico einaudi",
)

#: Setting up a contrast or reversal - the "but" structure.
CONTRAST = (
    "but ", "however", "until ", "turns out", "actually ", "instead ",
    " vs ", "versus", "before and after",
)


def _clean(text: str) -> str:
    """OCR output, flattened for matching.

    Frame-by-frame reads are joined with pipes and repeat heavily,
    and watermarks ("LE-CAFE", "YAMAHA") sit in the middle of real
    sentences. Lowercasing and collapsing whitespace is enough for
    phrase matching; stripping watermarks is not attempted, because
    a list of them would be guesswork and a false negative here only
    costs one unmatched device.
    """
    low = re.sub(r"[|]+", " ", (text or "").lower())
    return re.sub(r"\s+", " ", low).strip()


def devices(ocr_text: str | None) -> dict:
    """Which hook devices the on-screen text uses."""
    if not ocr_text or len(ocr_text.strip()) < 6:
        return {}
    low = _clean(ocr_text)

    found = {
        "hook_is_pov": any(p in low for p in POV),
        "hook_is_question": (
            "?" in low or any(low.startswith(q) or f" {q}" in low
                              for q in QUESTION_WORDS)),
        "hook_is_what_if": any(p in low for p in WHAT_IF),
        "hook_is_story": any(p in low for p in STORY),
        "hook_has_cta": any(p in low for p in CTA),
        "hook_is_instruction": any(p in low for p in INSTRUCTION),
        "hook_has_number_claim": bool(NUMBER_CLAIM.search(low)),
        "hook_names_celebrity": any(p in low for p in CELEBRITY),
        "hook_has_contrast": any(p in low for p in CONTRAST),
    }
    found["hook_device_count"] = float(sum(1 for v in found.values()
                                           if v is True))
    # A hook with no recognised device is a label - "Chopin Nocturne
    # op.9 no.2" - and that is itself a choice worth measuring.
    found["hook_is_plain_label"] = found["hook_device_count"] == 0
    found["hook_text_length"] = float(len(low))
    return found


BOOLEANS = frozenset({
    "hook_is_pov", "hook_is_question", "hook_is_what_if",
    "hook_is_story", "hook_has_cta", "hook_is_instruction",
    "hook_has_number_claim", "hook_names_celebrity",
    "hook_has_contrast", "hook_is_plain_label",
})
TEXTS: frozenset[str] = frozenset()


def describes(feature: str) -> str:
    return {
        "hook_is_pov": (
            "Whether the on-screen text puts the viewer in the scene - "
            "'POV:', 'when you', 'imagine'."),
        "hook_is_question": "Whether it asks the viewer something.",
        "hook_is_what_if": (
            "Whether it poses a counterfactual - 'what if X wrote Y'."),
        "hook_is_story": "Whether it tells a story rather than labelling.",
        "hook_has_cta": "Whether it asks the viewer to do something.",
        "hook_is_instruction": (
            "Whether it tells the viewer how to listen."),
        "hook_has_number_claim": (
            "Whether a number carries the hook - '40 years of practice'."),
        "hook_names_celebrity": (
            "Whether it names someone outside classical music."),
        "hook_has_contrast": (
            "Whether it sets up a reversal - 'but', 'until', 'turns out'."),
        "hook_device_count": "How many of these devices it stacks.",
        "hook_is_plain_label": (
            "Whether the text just names the piece, using no device."),
        "hook_text_length": "Characters of text read from the screen.",
    }.get(feature, feature)
