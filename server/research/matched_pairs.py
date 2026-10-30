"""Two of your videos that should have done the same, and did not.

The owner's own words, months before anything was built: find what
distinguishes "a video that is successful with respect to another
which is similar and draws no traffic". That is a matched-pair
design, and it is the nearest thing to a controlled comparison that
observation allows.

## Why it beats a plain correlation

A correlation across 600 publications asks "do videos with more X
do better on average". Everything else varies at the same time:
the piece, the platform, the year, how big the audience was that
month. The usual answer is to control for those statistically and
hope the model is right.

A matched pair controls by **construction**. Take two of the
owner's publications on the same platform, same kind of music,
close in length, posted close enough together that the audience was
about the same size - and one broke out while the other sank. Every
matched dimension is held still, so whatever differs between them
is a shorter list and a more interesting one.

## Why it is still not causal, and must never be presented as such

Matching removes the confounds we thought to match on. It cannot
remove the one that matters most here: the video he put more care
into is both the one with the better hook *and* the one with the
better everything-we-did-not-measure. A pair tells you what
differed. It does not tell you which difference did the work.

ADR 0011 makes this an explicit part of the evidence base rather
than a substitute for the experiment.

## What the arithmetic is

For each measurement, count the pairs where the winner had more of
it and the pairs where the winner had less. Under a null of no
relationship that is a coin flip, so a **two-sided sign test** is
the honest test - no distribution assumed, no effect size implied,
and it degrades gracefully when there are few pairs. Corrected
across the family with Benjamini-Hochberg, like everything else.

Ties are dropped rather than split, because a measurement that is
identical in both members of a pair carries no information about
that pair and inflating `n` with it would be flattering the result.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.db.models import Creator, MetricsSnapshot, Video
from server.features import lexicon as lex
from server.features.retention import benjamini_hochberg

logger = logging.getLogger("virallab.research.matched_pairs")

METHOD_VERSION = "matched-pairs-v1"

#: How far apart in robust-z two publications must be before the
#: pair is worth anything. Two videos that both did averagely have
#: nothing to teach, and pairing them would dilute every count.
MIN_GAP_Z = 1.5

#: How close in length, as a ratio. A 20-second reel and a
#: three-minute performance are different products, not a pair.
DURATION_RATIO = 1.75

#: How far apart in time, in days. Wide enough to find pairs in a
#: catalogue this size, narrow enough that the account has not
#: doubled in between.
MAX_DAYS_APART = 365

#: Below this many usable pairs a measurement is not reported.
#: Eight pairs all pointing one way is p = 0.008 on a sign test,
#: which is the least that should ever be shown.
MIN_PAIRS = 8

#: Matched on exactly. Each one is a thing we do not want to be the
#: explanation.
MATCH_ON = ("platform", "origin", "recognisability")


@dataclass
class Pair:
    winner_id: int
    loser_id: int
    winner_z: float
    loser_z: float
    basis: dict
    winner_caption: str = ""
    loser_caption: str = ""
    winner_views: int = 0
    loser_views: int = 0

    def as_dict(self) -> dict:
        return {
            "winner": {"video_id": self.winner_id,
                       "views": self.winner_views,
                       "caption": self.winner_caption[:140]},
            "loser": {"video_id": self.loser_id,
                      "views": self.loser_views,
                      "caption": self.loser_caption[:140]},
            "matched_on": self.basis,
            "gap": round(self.winner_z - self.loser_z, 2),
        }


def _sign_test_p(up: int, down: int) -> float | None:
    """Two-sided exact binomial at p = 0.5. No scipy in this project."""
    n = up + down
    if n == 0:
        return None
    from math import comb

    k = min(up, down)
    tail = sum(comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def _population(session: Session) -> tuple[dict, dict]:
    """The owner's publications, scored and described."""
    from server.research.baseline import Observation, rolling_baseline

    rows = session.execute(
        select(Video.id, Video.creator_id, Video.external_id,
               Video.posted_at, Video.caption, Video.platform,
               Video.duration_s, MetricsSnapshot.views,
               MetricsSnapshot.captured_at)
        .join(Creator, Creator.id == Video.creator_id)
        .join(MetricsSnapshot, MetricsSnapshot.video_id == Video.id)
        .where(Creator.is_own_account.is_(True),
               MetricsSnapshot.views.is_not(None),
               Video.posted_at.is_not(None))
        .order_by(Video.id, MetricsSnapshot.captured_at.desc())).all()

    seen: set[int] = set()
    by_creator: dict[int, list] = {}
    meta: dict[int, dict] = {}
    for (vid, cid, ext, posted, caption, platform, dur, views,
         captured) in rows:
        if vid in seen:
            continue
        seen.add(vid)
        by_creator.setdefault(cid, []).append(Observation(
            video_id=vid, external_id=ext or str(vid),
            published_at=posted, views=int(views),
            observed_at=captured, caption=caption))
        meta[vid] = {"posted_at": posted, "caption": caption or "",
                     "platform": platform, "duration_s": dur,
                     "views": int(views)}

    z: dict[int, float] = {}
    for obs in by_creator.values():
        obs.sort(key=lambda o: o.published_at)
        for point in rolling_baseline(obs):
            vid = point.observation.video_id
            if vid is not None and point.robust_z is not None:
                z[vid] = float(point.robust_z)
    return meta, z


def _descriptors(session: Session, ids: list[int]) -> dict[int, dict]:
    """The things a pair is matched ON, per publication."""
    from server.research import virality as vir

    feats = vir._feature_values(session, ids)
    origin = vir._origin_of(session)
    recog = feats.get("piece_recognisability", {})
    return {vid: {"origin": origin.get(vid, "unstated"),
                  "recognisability": recog.get(vid)}
            for vid in ids}


def build_pairs(session: Session, *, max_days: int = MAX_DAYS_APART,
                min_gap: float = MIN_GAP_Z) -> list[Pair]:
    """Every usable pair, each publication used at most once.

    Greedy on the largest gap first: the clearest contrasts get
    their partner before a weaker pairing can consume it. A
    publication appears in one pair at most, so no video can vote
    twice in the counts below.
    """
    meta, z = _population(session)
    ids = [v for v in meta if v in z]
    desc = _descriptors(session, ids)

    candidates: list[tuple[float, int, int, dict]] = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            gap = abs(z[a] - z[b])
            if gap < min_gap:
                continue
            ma, mb = meta[a], meta[b]
            if ma["platform"] != mb["platform"]:
                continue
            days = abs((ma["posted_at"] - mb["posted_at"]).days)
            if days > max_days:
                continue
            da, db = ma["duration_s"], mb["duration_s"]
            if da and db:
                ratio = max(da, db) / max(min(da, db), 1e-6)
                if ratio > DURATION_RATIO:
                    continue
            if desc[a]["origin"] != desc[b]["origin"]:
                continue
            if desc[a]["recognisability"] != desc[b]["recognisability"]:
                continue
            candidates.append((gap, a, b, {
                "platform": ma["platform"],
                "whose_music": desc[a]["origin"],
                "recognisability": desc[a]["recognisability"],
                "days_apart": days,
            }))

    candidates.sort(key=lambda c: -c[0])
    used: set[int] = set()
    pairs: list[Pair] = []
    for gap, a, b, basis in candidates:
        if a in used or b in used:
            continue
        used.update((a, b))
        win, lose = (a, b) if z[a] > z[b] else (b, a)
        pairs.append(Pair(
            winner_id=win, loser_id=lose,
            winner_z=z[win], loser_z=z[lose], basis=basis,
            winner_caption=meta[win]["caption"],
            loser_caption=meta[lose]["caption"],
            winner_views=meta[win]["views"],
            loser_views=meta[lose]["views"]))
    return pairs


def analyse(session: Session, **kwargs) -> dict:
    """What differs between the ones that flew and their twins."""
    from server.research import virality as vir

    pairs = build_pairs(session, **kwargs)
    if len(pairs) < MIN_PAIRS:
        return {
            "pairs": [p.as_dict() for p in pairs],
            "n_pairs": len(pairs), "differences": [],
            "why_empty": (
                f"Only {len(pairs)} pairs of your publications are "
                f"alike enough to compare. A pair needs the same "
                f"platform, the same kind of music, a similar "
                f"length, a posting date within a year, and one "
                f"clearly outperforming the other."),
            "how_it_works": _how_it_works(),
        }

    ids = [p.winner_id for p in pairs] + [p.loser_id for p in pairs]
    feats = vir._feature_values(session, ids)

    rows = []
    for feature, values in sorted(feats.items()):
        if feature in vir.TIME_PROXIES:
            continue
        up = down = 0
        for p in pairs:
            w, lo = values.get(p.winner_id), values.get(p.loser_id)
            if w is None or lo is None or w == lo:
                continue        # a tie carries no information
            if w > lo:
                up += 1
            else:
                down += 1
        if up + down < MIN_PAIRS:
            continue
        p_value = _sign_test_p(up, down)
        if p_value is None:
            continue
        rows.append({
            "feature": feature,
            "display_name": lex.display_name(feature),
            "winner_had_more": up, "winner_had_less": down,
            "pairs_compared": up + down,
            "direction": "more" if up > down else "less",
            "p_value": round(p_value, 5),
            "verified_enough": vir.may_be_a_finding(feature),
        })

    qs = benjamini_hochberg([r["p_value"] for r in rows])
    findings, held = [], []
    for row, q in zip(rows, qs):
        row["q_value"] = round(q, 5) if q is not None else None
        if q is None or q > vir.Q_THRESHOLD:
            continue
        row["plain"] = (
            f"In {row['pairs_compared']} matched pairs, the one that "
            f"did better had {row['direction']} "
            f"{row['display_name'].lower()} "
            f"{max(row['winner_had_more'], row['winner_had_less'])} "
            f"times.")
        (findings if row["verified_enough"] else held).append(row)

    findings.sort(key=lambda r: r["q_value"])
    # What came closest without surviving. Reported for the same
    # reason the rest of the project reports weak signals: with no
    # finding at all, "nothing is there" and "not enough pairs to
    # see it" look identical, and they call for opposite responses.
    closest = sorted((r for r in rows if r["p_value"] is not None),
                     key=lambda r: r["p_value"])[:8]
    for r in closest:
        r["plain"] = (
            f"The better video had {r['direction']} "
            f"{r['display_name'].lower()} in "
            f"{max(r['winner_had_more'], r['winner_had_less'])} of "
            f"{r['pairs_compared']} pairs.")
    return {
        "pairs": [p.as_dict() for p in pairs],
        "n_pairs": len(pairs),
        "measurements_tested": len(rows),
        "differences": findings,
        "not_verified_enough": held,
        "closest": [] if findings else closest,
        "closest_warning": (
            "None of these survived correction for testing many "
            "measurements at once. With 162 pairs a real effect has "
            "to be fairly lopsided to show, so this is as consistent "
            "with 'not enough pairs yet' as with 'nothing there' - "
            "and the two call for opposite responses."
            if not findings else ""),
        "how_it_works": _how_it_works(),
    }


def _how_it_works() -> dict:
    return {
        "what_a_pair_is": (
            "Two of your own publications on the same platform, the "
            "same kind of music, a similar length and within a year "
            "of each other - where one clearly beat your usual run "
            "and the other did not."),
        "why_bother": (
            "A correlation across everything you have posted has to "
            "allow for the piece, the platform and the year all "
            "varying at once. A pair holds those still, so whatever "
            "differs between the two is a shorter and more "
            "interesting list."),
        "the_test": (
            "For each measurement we count how often the better "
            "video had more of it. If it made no difference that is "
            "a coin flip, so the question is whether the count is "
            "too lopsided to be chance - corrected for testing many "
            "measurements at once."),
        "still_not_a_cause": (
            "Matching removes the differences we thought to match "
            "on. It cannot remove the big one: the video you put "
            "more care into is both the one with the better opening "
            "and the one with the better everything we did not "
            "measure. A pair says what differed, never which "
            "difference did the work."),
        "each_video_once": (
            "A publication appears in at most one pair, so no video "
            "can vote twice."),
    }
