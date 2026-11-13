"""Fetch other creators' videos to be judged. Runs ON Ubuntu.

The owner, after labelling 58 of his own: *"it will be probably
good to make a test on other people's videos with maybe a 100
videos, and leave mine alone."*

He is right, and the measurement says why. On his corpus, 87% of
publications show hands and 76% show a piano — he is a concert
pianist. A detector that answers "yes" every time is right 87% of
the time, and the best threshold OWLv2 can manage scores 85%.
**The detector is not losing to a better detector; it is losing
to the fact that the question is nearly always answered the same
way.** No sampling of his own videos fixes that, because it is a
property of his videos.

Other creators post from phones, in cars, to camera, with no
instrument in shot. That is where the negatives are.

## The six-hour window is the design, not an obstacle

ADR 0009 is explicit: a frame strip is media bytes, not derived
data, and competitor frames are not retained. What it permits is
this exact shape - acquire, extract, **label inside the window**,
release. The labels persist; the pixels do not.

That made re-fetching the benchmark cost $0.14 when the ADR was
written. Through the free route, which works again, it costs
nothing. So an unlabelled video whose window closes is not a
loss: press the button again.

## What it does not do

It does not stratify by detector score. His own holdout was
stratified because a pianist's videos are nearly all positives
and the only way to find the negatives was to go looking in the
strata where the detector was most confident. Other creators'
videos are expected to be naturally mixed, and a draw spread
across creators is both simpler and less likely to smuggle the
detector's opinion into the sample. If the balance comes back
lopsided anyway, that is worth knowing and stratifying then.

    python competitor_holdout.py --count 100            # plan only
    python competitor_holdout.py --count 100 --yes      # fetch
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/rastlyn/virallab")

from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from scripts.provision.sample_holdout import (  # noqa: E402
    DEFINITIONS_2, FIELDS_2, SCOPE_RULE_2)
from server.collectors import competitor_archive as ca  # noqa: E402
from server.db.models import (Creator, TransientMedia,  # noqa: E402
                              Video)

FIXTURE = Path("/home/rastlyn/virallab/tests/fixtures/vision"
               "/holdout2_competitor.json")

#: Holdout 2's two questions. The three this script originally
#: asked are retired: holdout 1 measured them on the owner's
#: account and found that 91% of his openings show hands and 99%
#: carry text, so a flawless answerer could not have cleared the
#: lift bar either. Face and sheet music are the two that vary, on
#: his cohort and on this one.
#: See docs/benchmarks/7f-holdout-2-protocol.md.
FIELDS = FIELDS_2

#: At most this many per creator, so 100 videos are 100 different
#: situations rather than one creator's habits measured 100 times.
MAX_PER_CREATOR = 3


def pick(session, count: int) -> list[int]:
    """Spread across creators, deterministic, newest creators last.

    Ordered by (creator, id) and taken round-robin so the sample
    is not forty videos by whoever happens to sort first.
    """
    rows = session.execute(
        select(Video.id, Video.creator_id)
        .join(Creator, Creator.id == Video.creator_id)
        .where(Creator.is_own_account.is_(False),
               Video.platform == "tiktok",
               Video.external_id.is_not(None))
        .order_by(Video.creator_id, Video.id)).all()
    by_creator: dict[int, list[int]] = {}
    for vid, cid in rows:
        by_creator.setdefault(cid, []).append(vid)

    out: list[int] = []
    for depth in range(MAX_PER_CREATOR):
        for cid in sorted(by_creator):
            got = by_creator[cid]
            if depth < len(got):
                out.append(got[depth])
                if len(out) >= count:
                    return out
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=80)
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    Session = sessionmaker(eng, expire_on_commit=False, future=True)

    with Session() as s:
        wanted = pick(s, args.count)
        held = set(s.scalars(
            select(TransientMedia.video_id).where(
                TransientMedia.video_id.in_(wanted),
                TransientMedia.deleted_at.is_(None))).all())
        creators = s.scalar(
            select(func.count(func.distinct(Video.creator_id)))
            .where(Video.id.in_(wanted))) or 0

    plan = {
        "wanted": len(wanted), "across_creators": creators,
        "already_held": len(held),
        "to_fetch": len(wanted) - len(held),
        "cost_usd": 0.0,
        "route": "yt-dlp, public and logged out (ADR 0010)",
        "window": ("Six hours from acquisition, then the media and "
                   "the frames go (ADR 0009). The labels stay. "
                   "Re-running this costs nothing."),
    }
    if not args.yes:
        plan["dry_run"] = "re-run with --yes to fetch"
        print(json.dumps(plan, indent=2))
        return 0
    print(json.dumps(plan, indent=2), flush=True)

    got, failed = list(held), []
    with Session() as s:
        for vid in wanted:
            if vid in held:
                continue
            video = s.get(Video, vid)
            creator = s.get(Creator, video.creator_id)
            status, detail = ca.acquire_one(s, video, creator)
            ca.remember(s, vid, status, detail)
            s.commit()
            if status == ca.SyncStatus.OK:
                got.append(vid)
            else:
                failed.append({"video_id": vid, "status": status,
                               "why": (detail or "")[:90]})
            if status == ca.SyncStatus.RATE_LIMITED:
                print(json.dumps({"stopped": detail}), flush=True)
                break
            print(f"  {vid}: {status}", flush=True)

    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps({
        "_what": ("Other creators' publications, to be judged while "
                  "their media is still held. Drawn "
                  f"{datetime.now(timezone.utc):%Y-%m-%d}."),
        "_why": (
            "The half that holdout 1 never had. A field measured on "
            "one account describes that account; promotion needs "
            "both halves, scored separately and never pooled "
            "(7b addendum 7.5). These two questions sit near 30% on "
            "both cohorts, so unlike piano and hands there is "
            "something here for a detector to be right or wrong "
            "about."),
        "_window": (
            "These are labellable only while the media is held. ADR "
            "0009: frames are media bytes and are not retained. The "
            "labels persist; re-fetching costs nothing."),
        "blind": True,
        "fields": list(FIELDS),
        "scope_rule": SCOPE_RULE_2,
        # Imported, not restated. Two halves judged under two
        # rulebooks would not be comparable, which is the whole
        # point of drawing both.
        "definitions": dict(DEFINITIONS_2),
        "labels": {},
        "to_label": [{"video_id": v, "field": f}
                     for v in sorted(got) for f in FIELDS],
    }, indent=1), encoding="utf-8")

    print(json.dumps({
        "result": {
            "held_and_labellable": len(got),
            "failed": len(failed),
            "why_not": failed[:8],
            "questions": len(got) * len(FIELDS),
            "fixture": str(FIXTURE),
            "spent_usd": 0.0,
        }}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
