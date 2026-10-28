"""The parameters that were never extracted, from data already held.

The owner asked for research across "repertoire, camera angle,
overlay, hooks, time of posting, and anything that can be possibly
put on a reel". Camera angle, overlay and hooks exist. Time of
posting, repertoire and anything about the caption had **zero
features** - never built, on either side of the comparison.

None of it needs a provider or a penny. 1,579 publications already
carry a post time, 1,573 a caption and 1,431 a duration, competitors
included. This turns that into measurements.

## Why this matters more than the visual work

Visual analysis of competitors needs their media, which costs money,
expires in six hours under ADR 0009 and is currently blocked by the
provider's monthly limit. **Everything in this file works on metadata
the system already has**, which means the competitor side of the
comparison can finally be more than a handful of creators.

## What is deliberately not inferred

**Time of posting is recorded in UTC.** Converting to the creator's
local time would need their timezone, which is not collected and
cannot be guessed from a handle. A posting-hour finding is therefore
about UTC hours, which is the right thing for the owner's own account
and a known weakness for creators elsewhere - stated rather than
papered over.

**Repertoire is detected from the caption, not known.** A caption
mentioning Chopin is evidence the video concerns Chopin, not proof.
The archive link in `report._owner_repertoire` is the stronger claim
and it covers almost nothing, so this is the weaker claim that covers
almost everything, and the two are kept apart.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import timezone

logger = logging.getLogger("virallab.features.publication_meta")

METHOD_VERSION = "publication-meta-v1"

#: Composers, with the spellings that actually appear in captions.
#: Transliteration varies - Rachmaninov, Rachmaninoff, Rachmaninow -
#: and a list that only knows one of them undercounts the composer
#: this owner plays most.
COMPOSERS: dict[str, tuple[str, ...]] = {
    "Chopin": ("chopin",),
    "Liszt": ("liszt",),
    "Beethoven": ("beethoven",),
    "Bach": ("bach", "j.s. bach", "js bach"),
    "Mozart": ("mozart",),
    "Debussy": ("debussy",),
    "Rachmaninov": ("rachmaninov", "rachmaninoff", "rachmaninow",
                    "rachmaninof"),
    "Schubert": ("schubert",),
    "Schumann": ("schumann",),
    "Brahms": ("brahms",),
    "Tchaikovsky": ("tchaikovsky", "tschaikowsky", "chaikovsky"),
    "Ravel": ("ravel",),
    "Satie": ("satie",),
    "Grieg": ("grieg",),
    "Mendelssohn": ("mendelssohn",),
    "Prokofiev": ("prokofiev", "prokofieff"),
    "Scriabin": ("scriabin", "skryabin"),
    "Haydn": ("haydn",),
    "Handel": ("handel", "haendel"),
    "Vivaldi": ("vivaldi",),
    "Albeniz": ("albeniz", "albéniz"),
    "Granados": ("granados",),
    "Gershwin": ("gershwin",),
    "Einaudi": ("einaudi",),
    "Yiruma": ("yiruma",),
    "Glass": ("philip glass",),
    "Shostakovich": ("shostakovich", "sjostakovitj"),
    "Saint-Saens": ("saint-saens", "saint saëns", "saint-saëns"),
    "Wagner": ("wagner",),
    "Verdi": ("verdi",),
    "Puccini": ("puccini",),
}

#: Forms a classical caption names. Used only to say whether a work
#: was named at all, not to identify which - that needs a catalogue
#: this project does not have.
WORK_WORDS = (
    "nocturne", "etude", "étude", "prelude", "preludio", "sonata",
    "ballade", "scherzo", "waltz", "valse", "polonaise", "mazurka",
    "impromptu", "fantasie", "fantasy", "rhapsody", "concerto",
    "symphony", "variations", "suite", "partita", "fugue", "toccata",
    "berceuse", "barcarolle", "intermezzo", "capriccio", "gymnopedie",
)

#: Phrases that ask the viewer to do something.
CTA_WORDS = (
    "follow", "comment", "share", "save this", "link in bio", "subscribe",
    "tag a", "let me know", "which one", "tell me", "drop a",
)

_HASHTAG = re.compile(r"#\w+", re.UNICODE)
_MENTION = re.compile(r"@\w+", re.UNICODE)


def _emoji_count(text: str) -> int:
    """Characters outside the usual writing systems.

    `unicodedata.category` of "So" catches most emoji without a
    dependency, which matters more than catching every one: the
    question is whether a caption is emoji-heavy, not exactly how
    many.
    """
    return sum(1 for ch in text if unicodedata.category(ch) == "So")


def _composer(text: str) -> str | None:
    low = text.lower()
    hits = [name for name, forms in COMPOSERS.items()
            if any(f in low for f in forms)]
    # Two composers named is a comparison or a programme, not a
    # repertoire label, so it is reported as the first rather than
    # pretending to a single answer.
    return hits[0] if hits else None


def extract(*, caption: str | None, posted_at, duration_s) -> dict:
    """Every non-visual measurement, from one publication's metadata.

    Returns only what the inputs support. A publication with no
    caption gets no caption features rather than zeros - a zero word
    count and a missing caption are different facts and storing them
    the same way would make "captions are getting shorter" mean
    nothing.
    """
    out: dict[str, object] = {}

    if posted_at is not None:
        at = posted_at
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        at = at.astimezone(timezone.utc)
        out["posted_hour_utc"] = float(at.hour)
        out["posted_weekday"] = float(at.weekday())      # 0 = Monday
        out["posted_is_weekend"] = at.weekday() >= 5

    if duration_s is not None:
        out["duration_s"] = float(duration_s)

    if caption is not None and caption.strip():
        text = caption.strip()
        tags = _HASHTAG.findall(text)
        # The caption with its hashtags stripped: a 30-hashtag block
        # is not prose, and counting it as words makes every
        # tag-stuffed caption look long.
        prose = _HASHTAG.sub(" ", text)
        prose = _MENTION.sub(" ", prose).strip()
        words = prose.split()
        first_line = next((ln.strip() for ln in text.splitlines()
                           if ln.strip()), "")
        low = text.lower()

        out |= {
            "caption_chars": float(len(text)),
            "caption_words": float(len(words)),
            "caption_first_line_words": float(len(first_line.split())),
            "hashtag_count": float(len(tags)),
            "mention_count": float(len(_MENTION.findall(text))),
            "caption_emoji_count": float(_emoji_count(text)),
            "caption_has_question": "?" in text,
            "caption_has_cta": any(w in low for w in CTA_WORDS),
            "caption_is_hashtags_only": len(words) == 0 and bool(tags),
        }
        composer = _composer(text)
        out["composer_named"] = composer is not None
        if composer:
            out["composer"] = composer
        out["work_form_named"] = any(w in low for w in WORK_WORDS)

    return out


#: Which extracted keys are booleans, so the writer stores them in
#: the right column rather than as 1.0 and 0.0.
BOOLEANS = frozenset({
    "posted_is_weekend", "caption_has_question", "caption_has_cta",
    "caption_is_hashtags_only", "composer_named", "work_form_named",
})

#: And which are text.
TEXTS = frozenset({"composer"})


def describes(feature: str) -> str:
    """One line a non-specialist can read, for the catalogue."""
    return {
        "posted_hour_utc": "The hour of day it went out, in UTC.",
        "posted_weekday": "Day of the week it went out, Monday = 0.",
        "posted_is_weekend": "Whether it went out on a Saturday or Sunday.",
        "duration_s": "How long the video is, in seconds.",
        "caption_chars": "How many characters the caption runs to.",
        "caption_words": "Words in the caption, not counting hashtags.",
        "caption_first_line_words": (
            "Words in the first line - the part a reader sees before "
            "tapping 'more'."),
        "hashtag_count": "How many hashtags the caption carries.",
        "mention_count": "How many accounts the caption tags.",
        "caption_emoji_count": "How many emoji the caption carries.",
        "caption_has_question": "Whether the caption asks anything.",
        "caption_has_cta": (
            "Whether the caption asks the viewer to do something - "
            "follow, comment, save."),
        "caption_is_hashtags_only": (
            "Whether the caption is hashtags and nothing else."),
        "composer_named": "Whether a composer is named in the caption.",
        "composer": "Which composer the caption names, where one is.",
        "work_form_named": (
            "Whether the caption names a form - nocturne, sonata, "
            "etude and so on."),
    }.get(feature, feature)
