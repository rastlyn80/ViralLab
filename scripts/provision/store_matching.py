"""Apply a matching payload to the authoritative database. Runs ON Ubuntu.

Replaces store_matches.py, which only ever upserted the masters that happened
to be matched. The server needs *every* master and its fingerprint so it can
match newly collected posts by itself, without the Windows machine being
awake.

Three rules this script exists to enforce:

  1. **A human decision is never overwritten.** Rows with `decided_by =
     'human'` are left exactly as they are - status, score, notes, all of it.
     A re-run refreshes evidence for everything else.
  2. **Stale machine links are pruned, human ones are not.** An earlier buggy
     run left 23 links behind that no longer corresponded to anything; those
     have to go, but only the ones no human ever touched.
  3. **Inferred performances are rebuilt, confirmed ones are kept.** Grouping
     improves as the fingerprint improves, so `status = 'inferred'` groups are
     disposable. Once the owner confirms a grouping it is data, not a guess.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/rastlyn/virallab")
from sqlalchemy import create_engine, delete, select, update  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from server.db.models import (LocalMaster, Performance,  # noqa: E402
                              Video, VideoMasterLink)

PAYLOAD = Path("/home/rastlyn/virallab/.matching_payload.json")
HUMAN_STATUSES = ("confirmed", "rejected", "variant")


def main() -> int:
    d = json.loads(PAYLOAD.read_text(encoding="utf-8"))
    eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    S = sessionmaker(eng, expire_on_commit=False, future=True)

    with S() as s:
        # ---- masters -------------------------------------------------
        master_id: dict[str, int] = {}
        created = updated = 0
        for m in d["masters"]:
            row = s.scalar(select(LocalMaster).where(
                LocalMaster.quick_id == m["quick_id"]))
            if row is None:
                row = LocalMaster(quick_id=m["quick_id"])
                s.add(row)
                created += 1
            else:
                updated += 1
            row.path = m["path"]
            row.filename = m["filename"]
            row.duration_s = m.get("duration_s")
            row.bytes = m.get("bytes")
            row.width = m.get("width")
            row.height = m.get("height")
            row.fingerprint = m.get("fingerprint")
            s.flush()
            master_id[m["quick_id"]] = row.id
        s.commit()
        print(f"  masters: {created} new, {updated} refreshed, "
              f"{len(master_id)} total")

        # ---- performances --------------------------------------------
        keep = s.scalars(select(Performance.id).where(
            Performance.status == "confirmed")).all()
        protected_masters = set(s.scalars(select(LocalMaster.id).where(
            LocalMaster.performance_id.in_(keep))).all()) if keep else set()
        # Detach masters from inferred groups, then drop those groups.
        stale = s.scalars(select(Performance.id).where(
            Performance.status == "inferred")).all()
        if stale:
            s.execute(update(LocalMaster)
                      .where(LocalMaster.performance_id.in_(stale))
                      .values(performance_id=None))
            s.execute(delete(Performance).where(Performance.id.in_(stale)))
            s.commit()
        print(f"  performances: dropped {len(stale)} inferred, "
              f"kept {len(keep)} confirmed")

        made = assigned = 0
        for g in d.get("performances", []):
            ids = [master_id[q] for q in g["members"] if q in master_id]
            # Never move a master that belongs to a confirmed grouping.
            ids = [i for i in ids if i not in protected_masters]
            if not ids:
                continue
            p = Performance(title=g["label"], status="inferred",
                            notes=g.get("notes"))
            s.add(p)
            s.flush()
            made += 1
            s.execute(update(LocalMaster)
                      .where(LocalMaster.id.in_(ids))
                      .values(performance_id=p.id))
            assigned += len(ids)
        s.commit()
        print(f"  performances: created {made}, assigned {assigned} master(s)")

        # ---- links ---------------------------------------------------
        now = datetime.now(timezone.utc)
        seen: set[tuple[int, int]] = set()
        ins = upd = kept_human = skipped = 0
        for lk in d["links"]:
            v = s.scalar(select(Video).where(
                Video.platform == lk["platform"],
                Video.external_id == lk["post_id"]))
            mid = master_id.get(lk["quick_id"])
            if v is None or mid is None:
                skipped += 1
                continue
            seen.add((v.id, mid))
            row = s.scalar(select(VideoMasterLink).where(
                VideoMasterLink.video_id == v.id,
                VideoMasterLink.master_id == mid))
            if row is not None and row.decided_by == "human":
                # Refresh nothing. The evidence that mattered is the evidence
                # the human saw; overwriting it would make the decision
                # unauditable.
                kept_human += 1
                continue
            if row is None:
                row = VideoMasterLink(video_id=v.id, master_id=mid)
                s.add(row)
                ins += 1
            else:
                upd += 1
            row.score = lk["score"]
            row.frame_overlap = lk.get("frame_overlap")
            row.duration_delta_s = lk.get("duration_delta_s")
            row.runner_up = lk.get("runner_up")
            row.status = lk["status"]
            row.method = lk.get("method", "dhash+audiofp-v3")
            row.audio_similarity = lk.get("audio_similarity")
            row.audio_method = lk.get("audio_method")
            row.audio_offset_s = lk.get("audio_offset_s")
            row.evidence = lk.get("evidence")
            row.decided_by = "matcher"
            row.decided_at = now
        s.commit()

        # Prune machine-set links this run no longer proposes.
        all_links = s.scalars(select(VideoMasterLink)).all()
        pruned = 0
        for row in all_links:
            if (row.video_id, row.master_id) in seen:
                continue
            if row.decided_by == "human" or row.status in HUMAN_STATUSES:
                continue
            s.delete(row)
            pruned += 1
        s.commit()

        print(f"  links: {ins} new, {upd} refreshed, {pruned} stale pruned, "
              f"{kept_human} human decision(s) left untouched, "
              f"{skipped} skipped (no such post/master)")

        counts = {}
        for row in s.scalars(select(VideoMasterLink)).all():
            counts[(row.status, row.decided_by)] = \
                counts.get((row.status, row.decided_by), 0) + 1
        print("  final link state:")
        for (st, by), n in sorted(counts.items()):
            print(f"    {st:<10} by {by or '?':<8} {n:>4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
