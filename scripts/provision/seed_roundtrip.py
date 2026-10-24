"""Seed a Phase 1 round-trip batch on the server. Run ON the Ubuntu box."""
import sys
sys.path.insert(0, "/home/rastlyn/virallab")
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import os
from server.db.models import Creator, Video
from server.queue.queue import enqueue_batch, queue_depth
from shared.schemas.jobs import Modality, Tier

# Seed the DISPOSABLE test database, never the application one:
# this script truncates, and it previously wrote to the live DB.
url = os.environ.get("VIRALLAB_TEST_DB_URL")
if not url:
    raise SystemExit("VIRALLAB_TEST_DB_URL not set; refusing to seed the application database")
eng = create_engine(url, future=True)
S = sessionmaker(eng, expire_on_commit=False, future=True)
with S() as s:
    for t in ("analysis_results","batch_videos","analysis_batches",
              "metrics_snapshots","videos","creators","worker_heartbeats"):
        s.execute(text(f"truncate {t} restart identity cascade"))
    c = Creator(platform="tiktok", handle="roundtrip_creator",
                follower_count=5000, is_own_account=False)
    s.add(c); s.flush()
    ids = []
    for i in range(5):
        v = Video(creator_id=c.id, platform="tiktok", external_id=f"rt{i}",
                  duration_s=30.0+i, source="provider",
                  media_status="absent")
        s.add(v); s.flush(); ids.append(v.id)
    b = enqueue_batch(s, modality=Modality.VISION, tier=Tier.T2,
                      method_version="phase1-roundtrip", video_ids=ids,
                      frames=4)
    s.commit()
    print(f"seeded batch {b.id} with {len(ids)} videos")
    print("queue depth:", queue_depth(s))
