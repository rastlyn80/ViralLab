"""How long should a video be? Asked in bands, because that is the
shape of the question.

The owner: *"I think we should also measure real length just to
see which duration is ideal for posts."*

Two things had to be checked before building anything.

**Is the length we hold the real length?** Measured 2026-11-04
against 100 competitor files held in quarantine: median
difference 0.30 s, never more than 1.0 s, and every difference a
rounding to whole seconds. `videos.duration_s` is the real
length. Nothing to re-measure.

**Has anyone already looked?** Yes, and that is the interesting
part. `duration_s` is tested by `virality.build` on both sides -
86 measurements on his 625 publications, 92 on 9,203 of other
people's - and it is **not a finding on either**. It does not
clear the threshold.

## Why that is not the answer he asked for

A rank correlation can only see a monotonic trend: longer is
better, or shorter is better. "Ideal" is neither. If sixty
seconds beats both fifteen and three minutes, a correlation
sees the rises and the falls cancel and reports nothing - which
is exactly what it reported.

So this bands the durations and compares each band against every
other publication, the same shape as `research/timing.py`,
two-sided Mann-Whitney with Benjamini-Hochberg across the
family.

## What makes this better evidence than the posting-time work

Posting time cannot be checked on other creators: we know the
instant they posted and not where they were, so their nine
o'clock and his are different moments. **Length has no such
problem.** Ninety seconds is ninety seconds on any account, so
this is one of the measurements that can meet the replication
bar in ADR 0011 - held on his account AND independently on other
creators, in the same direction.

## The confound, which does not go away

He does not choose length at random. A Chopin étude is ninety
seconds because the étude is ninety seconds. So a band that does
well carries some of *what is in it* as well as how long it is,
and no amount of data here separates those. Observed, and it
cannot be more.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.db.models import Creator, MetricsSnapshot, Video
from server.features.retention import benjamini_hochberg
from server.research.cohort import _mann_whitney_p

logger = logging.getLogger("virallab.research.length")

#: The bands, in seconds, and the ordinary words for them.
#:
#: Chosen from how short-form video is actually made and talked
#: about - the sub-fifteen clip, the half-minute, the minute, the
#: two-minute piece, the long one - and NOT from the data. A band
#: whose edges are placed where they maximise a difference is not
#: a band; it is the difference, drawn twice.
BANDS: tuple[tuple[str, float, float, str], ...] = (
    ("very short", 0.0, 15.0, "under 15 seconds"),
    ("short", 15.0, 30.0, "15 to 30 seconds"),
    ("about a minute", 30.0, 75.0, "30 to 75 seconds"),
    ("long", 75.0, 180.0, "75 seconds to 3 minutes"),
    ("very long", 180.0, float("inf"), "over 3 minutes"),
)

#: Below this a band is described and not tested.
MIN_PER_BAND = 12

Q_THRESHOLD = 0.10


def band_of(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    for name, lo, hi, _label in BANDS:
        if lo <= float(seconds) < hi:
            return name
    return None


def _scored(session: Session, *, own: bool) -> list[tuple[float, float]]:
    """(duration, robust z) for every publication we can score.

    The z is against the creator's own trailing run, as
    everywhere else, so a large account and a small one weigh the
    same and a long video is not flattered by its creator being
    popular.
    """
    from server.research.baseline import Observation, rolling_baseline

    rows = session.execute(
        select(Video.id, Video.creator_id, Video.external_id,
               Video.posted_at, Video.duration_s,
               MetricsSnapshot.views, MetricsSnapshot.captured_at)
        .join(Creator, Creator.id == Video.creator_id)
        .join(MetricsSnapshot, MetricsSnapshot.video_id == Video.id)
        .where(Creator.is_own_account.is_(own),
               MetricsSnapshot.views.is_not(None),
               Video.posted_at.is_not(None),
               Video.duration_s.is_not(None))
        .order_by(Video.id, MetricsSnapshot.captured_at.desc())).all()

    seen: set[int] = set()
    by_creator: dict[int, list[Observation]] = {}
    secs: dict[int, float] = {}
    for vid, cid, ext, posted, dur, views, captured in rows:
        if vid in seen:
            continue
        seen.add(vid)
        secs[vid] = float(dur)
        by_creator.setdefault(cid, []).append(Observation(
            video_id=vid, external_id=ext or str(vid),
            published_at=posted, views=int(views),
            observed_at=captured))

    out: list[tuple[float, float]] = []
    for obs in by_creator.values():
        obs.sort(key=lambda o: o.published_at)
        for point in rolling_baseline(obs):
            vid = point.observation.video_id
            if vid is None or point.robust_z is None:
                continue
            out.append((secs[vid], float(point.robust_z)))
    return out


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0


def _compare(scored: list[tuple[float, float]]) -> list[dict]:
    groups: dict[str, list[float]] = {}
    for seconds, z in scored:
        name = band_of(seconds)
        if name:
            groups.setdefault(name, []).append(z)

    labels = {b[0]: b[3] for b in BANDS}
    raw: list[dict] = []
    for name, _lo, _hi, _label in BANDS:
        mine = groups.get(name) or []
        rest = [v for k, vals in groups.items() if k != name
                for v in vals]
        entry = {
            "band": name, "clock": labels[name],
            "publications": len(mine),
            "median_vs_usual": (None if not mine
                                else round(_median(mine) or 0.0, 3)),
        }
        if len(mine) < MIN_PER_BAND or len(rest) < MIN_PER_BAND:
            entry["tested"] = False
            entry["p_value"] = None
            entry["why_not_tested"] = (
                f"{len(mine)} publications. Below {MIN_PER_BAND} this "
                f"is counted and described, not tested.")
        else:
            entry["tested"] = True
            entry["p_value"] = _mann_whitney_p(mine, rest)
        raw.append(entry)

    qs = benjamini_hochberg([e["p_value"] for e in raw])
    for entry, q in zip(raw, qs):
        entry["q_value"] = None if q is None else round(q, 4)
        entry["separates"] = bool(
            entry["tested"] and q is not None and q < Q_THRESHOLD)
    return raw


def build(session: Session) -> dict:
    """Which lengths went with doing better, on both sides."""
    mine = _compare(_scored(session, own=True))
    theirs = _compare(_scored(session, own=False))

    by_name = {t["band"]: t for t in theirs}
    agreed = []
    for m in mine:
        t = by_name.get(m["band"])
        if not (m["separates"] and t and t["separates"]):
            continue
        same = ((m["median_vs_usual"] or 0) > 0) == (
            (t["median_vs_usual"] or 0) > 0)
        if same:
            agreed.append(m["band"])

    return {
        "yours": mine,
        "other_creators": theirs,
        "holds_on_both": agreed,
        "grade": "Observed",
        "headline": _headline(mine, agreed),
        "why_bands_and_not_a_trend": (
            "A correlation can only find 'longer is better' or "
            "'shorter is better'. Asked that way, length is not a "
            "finding on either your account or anyone else's. But "
            "'ideal' is neither of those - if a minute beats both "
            "fifteen seconds and five minutes, the rise and the "
            "fall cancel and a correlation reports nothing. Bands "
            "can see a middle that a trend cannot."),
        "this_one_can_be_checked_on_others": (
            "Unlike posting time, which only means something in "
            "the poster's own local hour. Ninety seconds is ninety "
            "seconds on anybody's account, so a length that holds "
            "on both sides is the strongest kind of result this "
            "project produces."),
        "the_confound": (
            "You do not choose length at random. A piece is as "
            "long as the piece. So a band that does well carries "
            "some of what is IN it as well as how long it is, and "
            "nothing here separates those."),
        "the_bands": [{"name": b[0], "clock": b[3]} for b in BANDS],
        "where_the_bands_came_from": (
            "How short-form video is actually made, not from this "
            "data. Edges placed where they maximise a difference "
            "would not be bands; they would be the difference, "
            "drawn twice."),
    }


def _headline(mine: list[dict], agreed: list[str]) -> str:
    n = sum(e["publications"] for e in mine)
    if not n:
        return "No publication has both a length and a score yet."
    stand = [e for e in mine if e["separates"]]
    if not stand:
        return (f"Across {n} of your publications, no length band "
                f"separates from the rest once the bands are "
                f"corrected for each other. On this evidence, how "
                f"long a video is is not where your differences "
                f"are.")
    best = max(stand, key=lambda e: e["median_vs_usual"] or 0)
    worst = min(stand, key=lambda e: e["median_vs_usual"] or 0)
    bits = []
    if (best["median_vs_usual"] or 0) > 0:
        bits.append(f"{best['clock']} ran above your usual")
    if (worst["median_vs_usual"] or 0) < 0 and worst is not best:
        bits.append(f"{worst['clock']} below it")
    tail = (f" The same holds on other creators for "
            f"{', '.join(agreed)}, which is the strongest thing "
            f"this can say." if agreed else
            " It does not hold on other creators, so treat it as "
            "being about your account rather than about length.")
    return (f"Across {n} of your publications, "
            + " and ".join(bits) + "." + tail)
