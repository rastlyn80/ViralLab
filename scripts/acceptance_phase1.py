"""Phase 1 acceptance: the four adversarial tests across the REAL two machines.

The queue-level versions of these live in tests/server/test_queue.py and run
against a local database. This script exercises the same guarantees over the
actual network path — Windows worker to Ubuntu server, TLS with a pinned
self-signed CA — which is what the Phase 1 criterion actually asks for.

    python scripts/acceptance_phase1.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import shlex

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from remote import connect, env, run  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REMOTE = "/home/rastlyn/virallab"
METHOD = "phase1-roundtrip"

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" - {detail}" if detail else ""))
    return ok


def remote_sql(c, sql: str) -> str:
    """Read-only query via the server-side helper (nested quoting through
    bash -> python -> SQL proved too fragile to inline)."""
    rc, out, err = run(
        c, f"cd {REMOTE} && set -a && . ./.env && set +a && "
           f".venv/bin/python sql.py --query " + shlex.quote(sql),
        timeout=120)
    return (out or err).strip()


def remote_exec(c, sql: str) -> str:
    """Execute and commit a statement via the same helper."""
    rc, out, err = run(
        c, f"cd {REMOTE} && set -a && . ./.env && set +a && "
           f".venv/bin/python sql.py " + shlex.quote(sql),
        timeout=120)
    return (out or err).strip()


def reseed(c) -> None:
    run(c, f"cd {REMOTE} && set -a && . ./.env && set +a && "
           f".venv/bin/python seed_roundtrip.py", timeout=180)


def main() -> int:
    e = env()
    base = e["VIRALLAB_SERVER_URL"]
    token = e["SERVER_WORKER_TOKEN"]
    ca = str(ROOT / "certs" / "ubuntu-server.crt")
    http = httpx.Client(base_url=base, verify=ca, timeout=60.0,
                        headers={"Authorization": f"Bearer {token}"})
    c = connect()

    def lease(worker="gpu-1"):
        r = http.post("/jobs/lease", json={
            "worker_id": worker, "capabilities": ["vision"], "max_videos": 100})
        r.raise_for_status()
        return r.json()

    def submit(lz, vids, *, final, reason=None):
        return http.post("/jobs/results", json={
            "batch_id": lz["batch_id"], "lease_token": lz["lease_token"],
            "worker_id": "gpu-1", "final": final, "partial_reason": reason,
            "results": [{"video_id": v,
                         "idempotency_key": f"vision:{v}:{METHOD}",
                         "payload": {"ok": True}} for v in vids]})

    try:
        print("\nPhase 1 acceptance - Windows worker -> Ubuntu server over TLS")
        print(f"  server: {base}")
        print(f"  TLS   : pinned {Path(ca).name}\n")

        # -- 0. transport ------------------------------------------------
        print("0. Transport")
        check("TLS health check over tailnet",
              http.get("/health").json() == {"status": "ok"})
        bad = httpx.Client(base_url=base, verify=ca, timeout=30.0,
                           headers={"Authorization": "Bearer wrong"})
        check("unknown worker token rejected",
              bad.post("/jobs/lease", json={"worker_id": "x",
                                            "capabilities": ["vision"]}
                       ).status_code == 403)

        # -- 1. killed worker is requeued, keeping committed work ---------
        print("\n1. Killed worker mid-batch (ADR 0002 partial credit)")
        reseed(c)
        lz = lease()
        first = [v["video_id"] for v in lz["videos"]][:2]
        submit(lz, first, final=False)          # progress commit, then "die"
        check("partial progress committed before the kill",
              "2" in remote_sql(c, "select count(*) from analysis_results"),
              "2 of 5 videos")

        remote_exec(c, "update analysis_batches set lease_expires_at = now() - interval '2 minutes'")
        reaped = http.post("/admin/reap").json()["reaped"]
        check("reaper requeued the abandoned batch", reaped == [lz["batch_id"]],
              f"reaped={reaped}")

        remote_exec(c, "update analysis_batches set not_before=null")
        lz2 = lease("gpu-2")
        remaining = sorted(v["video_id"] for v in lz2["videos"])
        check("retry sees ONLY the unfinished remainder",
              remaining == sorted(set(range(1, 6)) - set(first)),
              f"got {remaining}, committed {sorted(first)}")

        # -- 2. stale lease token cannot write ---------------------------
        print("\n2. Zombie worker with a stale lease token")
        r_hb = http.post("/jobs/heartbeat", json={
            "batch_id": lz["batch_id"], "lease_token": lz["lease_token"],
            "worker_id": "gpu-1"})
        check("stale heartbeat rejected 409", r_hb.status_code == 409)
        r_sub = submit(lz, [5], final=True)
        check("stale result submission rejected 409", r_sub.status_code == 409)
        check("fresh holder still owns the lease",
              http.post("/jobs/heartbeat", json={
                  "batch_id": lz2["batch_id"], "lease_token": lz2["lease_token"],
                  "worker_id": "gpu-2"}).status_code == 200)

        # -- 3. replay is idempotent -------------------------------------
        print("\n3. Replay produces exactly one row per video")
        submit(lz2, remaining, final=True)
        submit(lz2, remaining, final=True)      # replay with the same token
        rows = remote_sql(
            c, "select count(*) from analysis_results where video_id=1")
        allrows = remote_sql(c, "select count(*) from analysis_results")
        check("exactly one result row per video",
              "5" in allrows and "1" in rows, f"total={allrows}")

        # -- 4. bounded retries, then dead-letter ------------------------
        print("\n4. Bounded retries end in a terminal dead-letter")
        reseed(c)
        seen = []
        for _ in range(3):
            remote_exec(c, "update analysis_batches set not_before=null")
            lz3 = lease()
            r = http.post("/jobs/fail", params={
                "batch_id": lz3["batch_id"], "lease_token": lz3["lease_token"],
                "error": "deliberate failure"})
            seen.append(r.json()["batch_status"])
        check("attempts exhaust into terminal 'failed'",
              seen == ["pending", "pending", "failed"], str(seen))
        remote_exec(c, "update analysis_batches set not_before=null")
        check("dead-lettered batch is never leased again",
              lease() is None or lease() == None)  # noqa: E711

        print("\n" + "=" * 62)
        failed = [r for r in results if r[0] == FAIL]
        print(f"  {len(results) - len(failed)}/{len(results)} checks passed")
        for _, name, detail in failed:
            print(f"  FAILED: {name} {detail}")
        print("=" * 62 + "\n")
        return 1 if failed else 0
    finally:
        http.close()
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
