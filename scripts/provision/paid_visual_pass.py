"""Look at competitors' videos through the provider. Runs ON Ubuntu.

The free route stopped working. `competitor_archive.py` asks
tiktok.com directly with `yt-dlp`, and TikTok has refused this
server since the end of October — measured on 2026-11-03,
`attempted: 1, acquired: 0, rate_limited: 1`, on the first request
of every pass.

This one is unaffected, because **this server never talks to
TikTok**. The provider fetches the file under its own contract and
we collect it from them. That is the arrangement
`competitor_media.py` was written to describe and the one ADR 0010
explicitly kept when it added the free route beside it.

The owner asked for alternatives and named proxies. This is the
alternative that needs no rule changed: ADR 0010 rule 2 — "a video
that does not resolve logged-out is recorded as unavailable and
skipped, never worked around" — is untouched, because nothing here
works around anything. We pay somebody whose job it is.

## Why a Python script and not a shell one

`competitor_visual_pass.sh` is the equivalent for the free route,
and it broke twice in two days: once because Windows line endings
reached the server, once because `tail -24` cut off the only lines
that said what had happened. Neither failure is possible here.

## The cycle, and why it is a cycle

Acquire a batch, derive the features, let the analyser release the
files, repeat. Downloading a thousand videos and then analysing
them would hold a thousand files at once — the disk could take it,
but the six-hour deletion guarantee is easier to keep honest when
the working set is forty.

## Money

Every batch reserves before it spends and the run stops the moment
the next batch would pass `--max-usd`. **$0.003 a video, measured
2026-11-03** `[PRIMARY]` from Apify's settled charge, so the
ceiling is in videos as much as dollars and both are printed
before anything runs. Nothing happens without `--yes`.

    python paid_visual_pass.py --target 100            # plan only
    python paid_visual_pass.py --target 100 --yes      # go
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, "/home/rastlyn/virallab")

from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from server.budget import budget_state  # noqa: E402
from server.config import SpendRefused  # noqa: E402
from server.collectors import competitor_media as cm  # noqa: E402
from server.db.models import Creator, TransientMedia, Video  # noqa: E402
from server.research.virality import MIN_PER_CREATOR  # noqa: E402

ROOT = "/home/rastlyn/virallab"
PYTHON = f"{ROOT}/.venv/bin/python"

#: `[PRIMARY, measured 2026-11-03]` Apify's settled charge for a
#: single-file run of the download add-on. Used to plan, never to
#: report: what a run cost is read back from the ledger.
MEASURED_USD_PER_VIDEO = 0.003

#: Files held at once. Small because the six-hour deletion promise
#: is easier to keep honest with a working set of forty than of a
#: thousand, not because the disk is tight.
BATCH = 40

#: A pass that is getting nothing is stopping, whatever the reason.
#: Three empty batches in a row and it gives up rather than
#: spending the ceiling on refusals.
EMPTY_BATCHES_BEFORE_GIVING_UP = 3

#: The share of a batch that must come back with media for the run
#: to be worth continuing.
#:
#: MEASURED on the first long run, 2026-11-03. Success per batch
#: of forty went 25, 23, 17, 17, 17, 17, 15, 11, 6, 4 - a clean
#: decline, because the provider's own access to TikTok degrades
#: under sustained use. A batch is billed for the run as well as
#: the files, so the tail of that run paid nearly as much per
#: batch for a sixth of the videos.
#:
#: `EMPTY_BATCHES_BEFORE_GIVING_UP` never fired: the rate
#: collapsed without ever reaching zero. Stopping at a quarter
#: keeps the productive part of a run and abandons the part that
#: is mostly paying for refusals.
#:
#: **Whether it recovers is not known.** The first version of this
#: note said "an hour later it starts high again", which was a
#: guess written as a fact. A second run half an hour after the
#: first opened at 4 of 40 - exactly where the first one ended -
#: so the degradation is not a per-run effect that resets. It may
#: be a daily allowance on the actor, or the platform refusing the
#: provider in turn. Nobody has measured it, and the only way to
#: find out is to try tomorrow and write down what happens.
#:
#: The single reason, for all 35 failures of that batch: "the
#: actor returned no mediaUrls; the download add-on may not have
#: run".
MIN_SUCCESS_RATE = 0.25


def _unanalysed(session):
    """Competitor TikTok publications that still need looking at.

    "Analysed" comes from `competitor_archive.analysed_ids`, which
    derives it from the stage list in `features/competitor.py`.
    Asking by feature NAME looked obvious and was wrong: the
    deterministic stages write `contrast_rms`,
    `camera_motion_px_per_frame` and the like, with no `vision_`
    prefix, so a first attempt at this query reported 41 freshly
    analysed videos as still missing and the whole backlog as
    larger than it is. One definition, in the module that owns
    the stages.
    """
    from server.collectors.competitor_archive import analysed_ids

    return (select(Video.id)
            .join(Creator, Creator.id == Video.creator_id)
            .where(Creator.is_own_account.is_(False),
                   Video.platform == "tiktok",
                   Video.external_id.is_not(None),
                   Video.id.not_in(analysed_ids(session))))


def _needs_looking_at(session, limit: int) -> list[Video]:
    """The next few to buy.

    Ordered by how much we already know about the creator, so the
    first videos bought are the ones that join an account we can
    already score against itself. A visual measurement on a
    creator with six publications tells us nothing: there is no
    within-creator comparison to put it in.
    """
    counts = (select(Video.creator_id,
                     func.count(Video.id).label("n"))
              .group_by(Video.creator_id).subquery())
    held = select(TransientMedia.video_id).where(
        TransientMedia.deleted_at.is_(None))
    rows = session.scalars(
        select(Video)
        .join(counts, counts.c.creator_id == Video.creator_id)
        .where(Video.id.in_(_unanalysed(session)),
               counts.c.n >= MIN_PER_CREATOR,
               Video.id.not_in(held))
        .order_by(counts.c.n.desc(), Video.id)
        .limit(limit)).all()
    return list(rows)


def _remaining(session) -> int:
    return session.scalar(
        select(func.count()).select_from(
            _unanalysed(session).subquery())) or 0


def _analyse() -> dict:
    """Derive features from whatever is in quarantine, then let it go."""
    proc = subprocess.run(
        [PYTHON, f"{ROOT}/scripts/provision/analyse_competitor_media.py",
         "--limit", str(BATCH * 2)],
        cwd=ROOT, capture_output=True, text=True, timeout=1800)
    out = proc.stdout or ""
    start = out.rfind("\n{")
    if start < 0:
        start = 0 if out.lstrip().startswith("{") else -1
    if start < 0:
        return {"analysed": None, "stderr": (proc.stderr or "")[-300:]}
    try:
        return json.loads(out[start:])
    except json.JSONDecodeError:
        return {"analysed": None, "note": "the analyser printed no JSON"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=100,
                    help="how many publications to look at")
    ap.add_argument("--max-usd", type=float, default=3.0,
                    help="stop before a batch would pass this")
    ap.add_argument("--yes", action="store_true",
                    help="actually spend; without it this only plans")
    args = ap.parse_args()

    engine = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    Session = sessionmaker(engine, expire_on_commit=False, future=True)
    token = os.environ["APIFY_TOKEN"]

    with Session() as s:
        left = _remaining(s)
        before = budget_state(s)
    target = max(1, min(args.target, left))
    plan = {
        "publications_without_any_visual_measurement": left,
        "target_this_run": target,
        "batch_size": BATCH,
        "measured_usd_per_video": MEASURED_USD_PER_VIDEO,
        "estimated_usd": round(target * MEASURED_USD_PER_VIDEO, 3),
        "ceiling_usd": args.max_usd,
        "why_this_route": (
            "The provider fetches the file; this server never talks "
            "to TikTok, which is why this works while the free route "
            "is refused."),
        "budget_before": before,
    }
    if not args.yes:
        plan["dry_run"] = "re-run with --yes to spend"
        print(json.dumps(plan, indent=2, default=str))
        return 0
    print(json.dumps(plan, indent=2, default=str))

    done = 0
    spent = 0.0
    empty = 0
    cycles: list[dict] = []
    # Every distinct reason, counted. The first version kept two
    # errors per cycle, so a run with 240 failures could account
    # for 20 of them and the question "why did most of that batch
    # not arrive" had no answer anywhere in the log.
    reasons: dict[str, int] = {}
    stopped = None
    started = time.monotonic()

    while done < target:
        if spent + BATCH * MEASURED_USD_PER_VIDEO > args.max_usd:
            stopped = (f"the next batch would pass the ${args.max_usd} "
                       f"ceiling for this run")
            break

        want = min(BATCH, target - done)
        with Session() as s:
            videos = _needs_looking_at(s, want)
            if not videos:
                stopped = "nothing left that needs it"
                break
            print(f"=== cycle {len(cycles) + 1}: "
                  f"{len(videos)} publication(s) ===", flush=True)
            try:
                out = cm.acquire(s, videos=videos, token=token,
                                 max_files=want)
            except SpendRefused as exc:
                # The cap doing its job. Not an error, and it must
                # not end the run in a traceback: the first long
                # pass died here at cycle 11 and printed a stack
                # trace where it should have printed what it had
                # achieved.
                s.rollback()
                stopped = f"the spending cap stopped this run: {exc}"
                break
            s.commit()
            billed = float(getattr(out, "billed_usd", 0.0) or 0.0)

        spent += billed
        got = out.acquired
        for err in out.errors:
            why = str(err.get("error", "unknown"))[:120]
            reasons[why] = reasons.get(why, 0) + 1
        analysed = _analyse()
        rate = got / len(videos) if videos else 0.0
        cycles.append({
            "requested": out.requested, "acquired": got,
            "failed": out.failed,
            "success_rate": round(rate, 3),
            "skipped_already_held": out.skipped_already_held,
            "billed_usd": round(billed, 4),
            "analysed": analysed.get("analysed"),
        })
        print(json.dumps(cycles[-1], indent=1, default=str), flush=True)

        if not got:
            empty += 1
            if empty >= EMPTY_BATCHES_BEFORE_GIVING_UP:
                stopped = (f"{empty} batches in a row acquired "
                           f"nothing; stopping rather than spending "
                           f"the ceiling on refusals")
                break
        else:
            empty = 0
            done += got
            if rate < MIN_SUCCESS_RATE:
                stopped = (
                    f"only {got} of {len(videos)} came back with "
                    f"media. Below {MIN_SUCCESS_RATE:.0%} a batch is "
                    f"mostly paying for refusals, so this stops "
                    f"rather than spending the ceiling. Whether the "
                    f"provider recovers, and how long it takes, has "
                    f"not been measured - try again tomorrow.")
                break

    with Session() as s:
        after = budget_state(s)
        still = _remaining(s)

    print(json.dumps({
        "result": {
            "looked_at": done,
            "spent_usd": round(spent, 4),
            "usd_per_video_looked_at": (
                round(spent / done, 5) if done else None),
            "stopped_because": stopped or "reached the target",
            "why_the_rest_did_not_arrive": dict(
                sorted(reasons.items(), key=lambda kv: -kv[1])[:6]),
            "cycles": cycles,
            "seconds": round(time.monotonic() - started, 1),
            "publications_still_without_a_visual_measurement": still,
            "finished_at": datetime.now(timezone.utc),
        },
        "budget_after": after,
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
