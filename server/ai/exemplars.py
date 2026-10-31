"""What other creators actually wrote, when it worked unusually well.

The owner's requirement, in his words: an AI "trained exactly on
all the findings" that "can actually access the best practices from
competitors' videos in terms of hooks, content". The findings half
existed. This is the other half.

A finding says *naming the composer went with doing better*. It
does not say what that looks like in eleven words on a screen. The
generator had the owner's own best openings verbatim and nothing
from anyone else, so it could rearrange his voice and never show
him a shape he had not already tried - which is the one thing
other creators are for.

## Ranked within the creator, never by views

`pianoescu` has a video at 69 million. Sorting by views returns
that account's catalogue and calls it best practice. Every example
here is chosen by how far it beat **its own creator's** other
videos, so a 400-follower pianist whose video did ten times their
usual appears beside a star whose video did ten times theirs - and
both earned it the same way.

Views are still shown, because "this worked for someone with two
thousand followers" and "this worked for someone with two million"
are different facts and the owner should see which he is reading.

## What the model may do with them

Learn the shape. Never the words.

These are other people's captions and other people's on-screen
text. The rule is enforced after generation, in `creative.review`,
because a prompt is not a boundary - but it is stated here too,
because the reason matters: a suite that recycles a competitor's
sentence is not doing research, it is doing plagiarism with extra
steps, and it would be obvious to the one audience that follows
both accounts.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger("virallab.ai.exemplars")

#: How far above its creator's own median a publication must sit
#: before it counts as an example of anything. 0.85 keeps the top
#: sixth of each catalogue.
MIN_PERCENTILE = 0.85

#: A creator needs this many publications before a percentile
#: within them means anything. Matches `virality.MIN_PER_CREATOR`.
MIN_PER_CREATOR = 8

#: Per creator, so one prolific account cannot supply the whole
#: list and turn "what works" into "what this person does".
MAX_PER_CREATOR = 2


_OPENINGS = text("""
with scored as (
  select c.handle, c.platform, v.id as video_id,
         m.views,
         f.value_text as body,
         percent_rank() over (partition by v.creator_id
                              order by m.views) as pr,
         count(*) over (partition by v.creator_id) as catalogue,
         row_number() over (partition by v.creator_id
                            order by m.views desc) as rank_in_creator
  from video_features f
  join videos v on v.id = f.video_id
  join creators c on c.id = v.creator_id
  join lateral (
    select views from metrics_snapshots ms
    where ms.video_id = v.id and ms.views is not null
    order by ms.captured_at desc limit 1
  ) m on true
  where not c.is_own_account
    and f.feature = :feature
    and f.status = 'ok'
    and f.value_text is not null
    and length(f.value_text) > :min_len
)
select handle, platform, video_id, views, body, pr
from scored
where pr >= :min_pr
  and catalogue >= :min_cat
  and rank_in_creator <= :max_per
order by pr desc, views desc
limit :limit
""")


_CAPTIONS = text("""
with scored as (
  select c.handle, c.platform, v.id as video_id,
         m.views,
         v.caption as body,
         percent_rank() over (partition by v.creator_id
                              order by m.views) as pr,
         count(*) over (partition by v.creator_id) as catalogue,
         row_number() over (partition by v.creator_id
                            order by m.views desc) as rank_in_creator
  from videos v
  join creators c on c.id = v.creator_id
  join lateral (
    select views from metrics_snapshots ms
    where ms.video_id = v.id and ms.views is not null
    order by ms.captured_at desc limit 1
  ) m on true
  where not c.is_own_account
    and v.caption is not null
    and length(v.caption) > :min_len
)
select handle, platform, video_id, views, body, pr
from scored
where pr >= :min_pr
  and catalogue >= :min_cat
  and rank_in_creator <= :max_per
order by pr desc, views desc
limit :limit
""")


def _tidy(body: str) -> str:
    """On-screen text, made readable.

    Frame-by-frame reads repeat and are joined with pipes, so a raw
    value is "Hall3 | Hall3 | Hall2 | The most EPIC | PIANO
    BATTLE". Consecutive duplicates go; the order stays, because
    the order is the hook.
    """
    parts = [p.strip() for p in (body or "").split("|")]
    out: list[str] = []
    for part in parts:
        if part and (not out or part.lower() != out[-1].lower()):
            out.append(part)
    return " / ".join(out)


#: Tokens that are a chord, a key or a bar number rather than a
#: word. An OCR pass over a score or a chord chart returns dozens.
_NOT_WORDS = {"eb", "ab", "bb", "db", "gb", "ad", "am", "dm", "em",
              "bm", "cm", "fm", "gm", "maj", "min", "sus", "fyp",
              "fy", "viral", "foryou", "foryoupage"}


def _is_language(body: str) -> bool:
    """Whether this reads as a sentence rather than as debris.

    Three things get through the length filter and should not be
    shown to a model as best practice:

    * **a chord chart** - "Eb / Ab / AD / Bb / Ab / Eb" was the
      second-highest-performing opening in the corpus and is an OCR
      pass over sheet music;
    * **a truncated read** - "oductiveTract / Bro gave up studying
      m", where the frame cut the words in half;
    * **a hashtag block** - "#piano #pianotok #fyp #goldenbrown",
      which is a caption in the sense that it occupies the field.

    A model handed these learns nothing and may imitate them.
    """
    words = [w.strip(".,!?:;\"'()[]").lower()
             for w in (body or "").replace("/", " ").split()]
    real = [w for w in words
            if len(w) >= 3 and w.isalpha() and w not in _NOT_WORDS]
    if len(real) < 4:
        return False
    tags = sum(1 for w in words if w.startswith("#") or w.startswith("@"))
    if tags and tags / max(len(words), 1) > 0.5:
        return False
    # Mostly non-Latin: real, and not something this owner can learn
    # a phrasing from.
    letters = [ch for ch in body if ch.isalpha()]
    if letters and sum(1 for ch in letters if ord(ch) < 128) / len(letters) < 0.7:
        return False
    return True


def _rows(session: Session, stmt, *, feature: str | None,
          limit: int, min_len: int) -> list[dict]:
    params = {"min_pr": MIN_PERCENTILE, "min_cat": MIN_PER_CREATOR,
              "max_per": MAX_PER_CREATOR, "limit": limit,
              "min_len": min_len}
    if feature is not None:
        params["feature"] = feature
    seen: set[str] = set()
    out: list[dict] = []
    for r in session.execute(stmt, params):
        body = _tidy(r.body) if feature else " ".join(r.body.split())
        key = body[:60].lower()
        if not body or key in seen or not _is_language(body):
            continue
        seen.add(key)
        out.append({
            "text": body[:220],
            "views": int(r.views),
            "did_better_than": f"{round(r.pr * 100)}% of that "
                               f"creator's own videos",
            "platform": r.platform,
        })
    return out


def openings(session: Session, limit: int = 14) -> list[dict]:
    """Other creators' on-screen text, where it outperformed."""
    return _rows(session, _OPENINGS, feature="ocr_text",
                 limit=limit, min_len=18)


def captions(session: Session, limit: int = 12) -> list[dict]:
    """Other creators' captions, where they outperformed."""
    return _rows(session, _CAPTIONS, feature=None,
                 limit=limit, min_len=40)


def gather(session: Session) -> dict:
    """Both, with the caveat that travels with them."""
    try:
        heads = openings(session)
        caps = captions(session)
    except Exception:                       # noqa: BLE001 - reported
        logger.exception("could not gather competitor exemplars")
        return {"openings": [], "captions": [], "how_chosen": "",
                "rule": ""}
    return {
        "openings": heads,
        "captions": caps,
        "how_chosen": (
            "Chosen by how far each beat its OWN creator's other "
            "videos, not by view count, so a small account's "
            "breakout sits beside a large one's. At most two per "
            "creator."),
        "rule": (
            "These are other people's words. Learn the shape; never "
            "reuse the sentence."),
    }
