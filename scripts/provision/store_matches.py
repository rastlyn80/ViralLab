"""Store local masters and their post links on the server. Runs ON Ubuntu."""
import json, os, sys
from pathlib import Path
sys.path.insert(0, "/home/rastlyn/virallab")
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from server.db.models import LocalMaster, Video, VideoMasterLink

P = Path("/home/rastlyn/virallab/.matches_payload.json")
d = json.loads(P.read_text(encoding="utf-8"))
eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
S = sessionmaker(eng, expire_on_commit=False, future=True)

with S() as s:
    masters = {}
    for m in d["masters"]:
        row = s.scalar(select(LocalMaster).where(
            LocalMaster.quick_id == m["quick_id"]))
        if row is None:
            row = LocalMaster(quick_id=m["quick_id"])
            s.add(row)
        row.path = m["path"]; row.filename = m["filename"]
        row.duration_s = m.get("duration_s"); row.bytes = m.get("bytes")
        row.width = m.get("width"); row.height = m.get("height")
        row.fingerprint = m.get("fingerprint")
        s.flush()
        masters[m["quick_id"]] = row.id
    print(f"  local_masters upserted: {len(masters)}")

    linked = skipped = 0
    for lk in d["links"]:
        v = s.scalar(select(Video).where(
            Video.platform == lk["platform"],
            Video.external_id == lk["post_id"]))
        mid = masters.get(lk["quick_id"])
        if v is None or mid is None:
            skipped += 1; continue
        existing = s.scalar(select(VideoMasterLink).where(
            VideoMasterLink.video_id == v.id,
            VideoMasterLink.master_id == mid))
        if existing is None:
            existing = VideoMasterLink(video_id=v.id, master_id=mid)
            s.add(existing)
        # Never downgrade a human decision back to an automatic one.
        if existing.status not in ("confirmed", "rejected"):
            existing.status = lk["status"]
        existing.score = lk["score"]
        existing.frame_overlap = lk.get("frame_overlap")
        existing.duration_delta_s = lk.get("duration_delta_s")
        existing.runner_up = lk.get("runner_up")
        linked += 1
    # Drop links this run no longer supports. Without this, a false positive
    # from an earlier, buggier matcher would survive forever as 'auto'.
    # Human decisions are never discarded.
    current = set()
    for lk in d["links"]:
        v = s.scalar(select(Video).where(
            Video.platform == lk["platform"],
            Video.external_id == lk["post_id"]))
        mid = masters.get(lk["quick_id"])
        if v is not None and mid is not None:
            current.add((v.id, mid))
    stale = [l for l in s.scalars(select(VideoMasterLink)).all()
             if (l.video_id, l.master_id) not in current
             and l.status not in ("confirmed", "rejected")]
    for l in stale:
        s.delete(l)
    s.commit()
    print(f"  links written: {linked} (skipped {skipped}), "
          f"stale removed: {len(stale)}")

    for st in ("auto", "pending", "confirmed", "rejected"):
        n = s.query(VideoMasterLink).filter(
            VideoMasterLink.status == st).count()
        if n:
            print(f"    {st:<10} {n}")
