import os, sys
sys.path.insert(0, "/home/rastlyn/virallab")
from sqlalchemy import create_engine, text
e = create_engine(os.environ["VIRALLAB_DB_URL"])
with e.connect() as c:
    b = c.execute(text("select id,status,attempt_count,peak_vram_mib,leased_by,last_error from analysis_batches order by id")).all()
    for r in b: print(f"  batch {r.id}: status={r.status} attempt={r.attempt_count} peak_vram={r.peak_vram_mib} by={r.leased_by} err={(r.last_error or '')[:55]}")
    print("  results:", c.execute(text("select count(*) from analysis_results")).scalar())
    print("  batch_videos:", dict(c.execute(text("select state,count(*) from batch_videos group by state")).all()))
    for r in c.execute(text("select worker_id,state,reason,free_vram_mib from worker_heartbeats")).all():
        print(f"  worker {r.worker_id}: {r.state} - {(r.reason or '')[:50]} (free={r.free_vram_mib})")
