"""Repertoire, cadence and novelty — free, and on both sides.

BLUEPRINT §8.12 ranks what to build next by evidence gained per
pound, and the top item is everything that needs no media: it
roughly doubles the parameter count on **both** sides of the
comparison, which is the half that has been starving.

Three groups, all derived from data already held.

## Repertoire — the dimension the owner named first

`publication_meta` detects a composer in 450 captions and stops
there. A composer is not repertoire. What a listener actually meets
is a *piece*, and pieces differ enormously in how much work they do
for you: the Nocturne op.9 no.2 arrives with its own audience, and
the Scriabin fifth sonata does not.

So this adds the period the composer belongs to, and a
**recognisability tier** - whether a lay scroller would know the
piece within three seconds. That tier is a judgement, written down
as a list rather than inferred, and it is the single most useful
thing on this page for a concert pianist, because it separates "this
did well" from "this piece always does well".

## Cadence — when, and how regularly

Days since the previous post, and how regular the account's rhythm
is. An account that posts every other day and one that posts twice a
year are different distribution problems, and neither the owner's
nor a competitor's cadence was measured.

## Novelty against the creator's own catalogue

Untouched anywhere in the project and plausibly among the strongest
terms: the same video from an account that always posts it is a
different event from one that never does. Measured as whether this
composer, this form and this content shape have appeared in that
creator's recent publications.

**Novelty is relative and therefore order-dependent.** It is
computed from what the creator had *already posted at that moment*,
never from their whole catalogue - using later videos to describe an
earlier one would leak the future into a feature, which CLAUDE.md
§3.9 exists to prevent.
"""

from __future__ import annotations

import logging
import statistics
from datetime import datetime

logger = logging.getLogger("virallab.features.context")

METHOD_VERSION = "context-v2"

#: Composer to period. Coarse on purpose: the question is whether a
#: scroller hears "old formal music" or "film-score piano", not
#: whether a musicologist would accept the boundary.
PERIOD: dict[str, str] = {
    "Bach": "baroque", "Handel": "baroque", "Vivaldi": "baroque",
    "Haydn": "classical", "Mozart": "classical",
    "Beethoven": "classical_romantic",
    "Schubert": "romantic", "Chopin": "romantic", "Liszt": "romantic",
    "Schumann": "romantic", "Brahms": "romantic",
    "Mendelssohn": "romantic", "Tchaikovsky": "romantic",
    "Grieg": "romantic", "Verdi": "romantic", "Wagner": "romantic",
    "Saint-Saens": "romantic", "Albeniz": "romantic",
    "Granados": "romantic",
    "Debussy": "impressionist", "Ravel": "impressionist",
    "Satie": "impressionist",
    "Rachmaninov": "late_romantic", "Scriabin": "late_romantic",
    "Puccini": "late_romantic",
    "Prokofiev": "modern", "Shostakovich": "modern",
    "Gershwin": "modern", "Glass": "contemporary",
    "Einaudi": "contemporary", "Yiruma": "contemporary",
}

#: Pieces a scrolling non-musician recognises in about three seconds.
#: A judgement, written down so it can be argued with rather than
#: buried in a model. The owner is the right person to correct it.
#:
#: Tier 1 carries itself: the piece is the hook. Tier 2 is known to
#: anyone who likes classical music but not to a general audience.
#: Everything else is tier 3, which is most of the repertoire and
#: most of what a serious pianist actually plays.
TIER_1: tuple[tuple[str, ...], ...] = (
    ("fur elise", "für elise", "furelise"),
    ("moonlight sonata", "moonlight", "mondschein"),
    ("clair de lune", "claire de lune"),
    ("canon in d", "pachelbel"),
    ("nocturne op. 9", "nocturne op.9", "nocturne op 9", "nocturne in e flat"),
    ("turkish march", "rondo alla turca", "alla turca"),
    ("ode to joy",),
    ("flight of the bumblebee", "bumblebee"),
    ("the entertainer",),
    ("river flows in you",),
    ("comptine d", "amelie"),
    ("interstellar", "cornfield chase"),
    ("married life", "up theme"),
    ("hallelujah",),
    ("prelude in c", "well-tempered clavier book 1 no. 1"),
    ("gymnopedie", "gymnopédie"),
    ("la vie en rose",),
    ("merry christmas mr lawrence",),
)
TIER_2: tuple[tuple[str, ...], ...] = (
    ("ballade no. 1", "ballade no 1", "ballade 1", "g minor ballade"),
    ("liebestraum",),
    ("la campanella", "campanella"),
    ("prelude in c sharp minor", "prelude op. 3 no. 2"),
    ("revolutionary etude", "revolutionary étude", "op. 10 no. 12"),
    ("fantaisie-impromptu", "fantaisie impromptu"),
    ("arabesque",),
    ("appassionata",),
    ("pathetique", "pathétique"),
    ("waldstein",),
    ("hungarian rhapsody",),
    ("winter wind",),
    ("black key", "op. 10 no. 5"),
    ("raindrop prelude", "raindrop"),
    ("rhapsody in blue",),
    ("bohemian rhapsody",),
    ("mephisto waltz",),
    ("un sospiro",),
    ("chopin heroic", "heroic polonaise", "polonaise op. 53"),
)


#: Phrases a performer uses when the music is theirs. First person
#: matters: "Chopin composed this" is not a claim of authorship and
#: a bare "composition" appears in plenty of repertoire captions.
OWN_WORK = (
    "my composition", "my own composition", "my piece", "my own piece",
    "my original", "original composition", "i composed", "i wrote",
    "composed by me", "written by me", "my music", "my new piece",
    "original music", "original piano", "#originalmusic",
    "#originalcomposition", "#mycomposition", "#originalsong",
)
IMPROVISED = (
    "improvis", "improv ", "#improv", "made this up", "on the spot",
)
ARRANGED = (
    "my arrangement", "arranged by me", "i arranged", "my version of",
    "my take on", "my cover", "#arrangement",
)


def origin(caption: str | None, composer: str | None) -> dict:
    """Whose music this is: repertoire, the performer's own, or neither.

    The owner raised this and it is not a bookkeeping distinction.
    A Chopin nocturne arrives with its own audience - the piece does
    some of the work. An original composition does none of that, and
    has to earn attention from the first bar with nothing borrowed.
    So the two are different problems and what makes one break out
    is not necessarily what makes the other.

    Both matter to this owner, who plays both and wants both to
    travel. Lumping them together means every finding is an average
    over two populations that behave differently, which is how a
    real effect in one of them gets cancelled by its absence in the
    other.
    """
    out: dict[str, object] = {}
    if not caption:
        return out
    low = caption.lower()
    own = any(w in low for w in OWN_WORK)
    improvised = any(w in low for w in IMPROVISED)
    arranged = any(w in low for w in ARRANGED)

    out["is_improvisation"] = improvised
    out["is_arrangement"] = arranged and not own
    out["is_own_composition"] = own or improvised

    if own or improvised:
        kind = "own_composition"
    elif arranged:
        kind = "arrangement"
    elif composer:
        kind = "repertoire"
    else:
        # No composer named and no claim of authorship. Most often a
        # performance whose caption simply does not say, and calling
        # it either would be inventing the answer.
        kind = "unstated"
    out["content_origin"] = kind
    return out


def _tier(caption: str) -> int | None:
    low = caption.lower()
    for forms in TIER_1:
        if any(f in low for f in forms):
            return 1
    for forms in TIER_2:
        if any(f in low for f in forms):
            return 2
    return None


def repertoire(caption: str | None, composer: str | None) -> dict:
    """What was played, as far as the caption admits."""
    out: dict[str, object] = {}
    if composer:
        period = PERIOD.get(composer)
        if period:
            out["composer_period"] = period
        out["composer_is_mainstream"] = composer in {
            "Chopin", "Beethoven", "Mozart", "Bach", "Debussy", "Liszt",
            "Einaudi", "Yiruma", "Satie"}
    if caption:
        tier = _tier(caption)
        # Tier 3 - "a piece nobody recognises" - is the default and
        # is recorded explicitly, because "no tier detected" and
        # "obscure" are the same fact here and a null would read as
        # missing data.
        out["piece_recognisability"] = float(tier or 3)
        out["piece_is_widely_known"] = tier == 1
    return out


def cadence(posted_at: datetime, previous: list[datetime]) -> dict:
    """When this went out, relative to what came before it.

    `previous` is only the publications that already existed at the
    time - never the whole catalogue. Describing a video with what
    its creator did afterwards leaks the future into a feature.
    """
    out: dict[str, object] = {}
    if not previous:
        out["is_first_post"] = True
        return out
    out["is_first_post"] = False
    last = max(previous)
    gap = (posted_at - last).total_seconds() / 86400.0
    out["days_since_previous_post"] = round(max(gap, 0.0), 3)
    out["publications_before_this"] = float(len(previous))

    if len(previous) >= 4:
        ordered = sorted(previous)
        gaps = [(b - a).total_seconds() / 86400.0
                for a, b in zip(ordered, ordered[1:])]
        gaps = [g for g in gaps if g > 0]
        if len(gaps) >= 3:
            med = statistics.median(gaps)
            out["usual_gap_days"] = round(med, 3)
            # How far off their own rhythm this one was. A creator
            # who posts daily and then vanishes for a month is doing
            # something different from one who always posts monthly,
            # and the raw gap cannot tell them apart.
            if med > 0:
                out["gap_vs_usual"] = round(gap / med, 3)
    return out


def novelty(*, composer: str | None, form_named: bool,
            recent_composers: list[str | None],
            recent_forms: list[bool]) -> dict:
    """How unlike the creator's recent work this is.

    Only `recent_*` - what they had posted before this one - for the
    same reason as `cadence`.
    """
    out: dict[str, object] = {}
    if not recent_composers:
        return out
    seen = [c for c in recent_composers if c]
    out["composer_repeated_recently"] = bool(composer and composer in seen)
    out["distinct_composers_recently"] = float(len(set(seen)))
    if composer:
        # How much of their recent output was this same composer. A
        # pianist who posts nothing but Chopin is a different
        # proposition from one whose Chopin video is a departure.
        same = sum(1 for c in recent_composers if c == composer)
        out["share_recent_same_composer"] = round(
            same / len(recent_composers), 3)
    if recent_forms:
        out["form_repeated_recently"] = bool(form_named and any(recent_forms))
    return out


#: How many preceding publications count as "recent". Short enough
#: that a creator's style drift does not swamp it, long enough that
#: a single unusual week does not.
RECENT_WINDOW = 10

BOOLEANS = frozenset({
    "composer_is_mainstream", "piece_is_widely_known", "is_first_post",
    "composer_repeated_recently", "form_repeated_recently",
    "is_improvisation", "is_arrangement", "is_own_composition",
})
TEXTS = frozenset({"composer_period", "content_origin"})


def describes(feature: str) -> str:
    return {
        "composer_period": "Which period the composer belongs to.",
        "composer_is_mainstream": (
            "Whether the composer is one a general audience has heard "
            "of."),
        "piece_recognisability": (
            "How likely a scrolling non-musician is to recognise the "
            "piece: 1 means instantly, 3 means not at all."),
        "piece_is_widely_known": (
            "Whether this is one of the handful of pieces that carry "
            "their own audience."),
        "is_first_post": "Whether this was the account's first post.",
        "days_since_previous_post": "Days since the last publication.",
        "publications_before_this": (
            "How many the account had already posted."),
        "usual_gap_days": "The account's usual gap between posts.",
        "gap_vs_usual": (
            "This gap against their usual one. Above 1 means they had "
            "been quiet for longer than normal."),
        "composer_repeated_recently": (
            "Whether this composer appeared in their last ten posts."),
        "distinct_composers_recently": (
            "How many different composers their last ten posts named."),
        "share_recent_same_composer": (
            "What share of their recent posts named this same "
            "composer."),
        "form_repeated_recently": (
            "Whether they had recently posted the same kind of piece."),
        "is_own_composition": (
            "Whether the performer says the music is theirs."),
        "is_improvisation": "Whether it is described as improvised.",
        "is_arrangement": (
            "Whether it is the performer's arrangement of someone "
            "else's piece."),
        "content_origin": (
            "Whose music it is: repertoire, the performer's own, an "
            "arrangement, or not stated."),
    }.get(feature, feature)
