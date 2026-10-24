"""Windows GPU worker: poll, lease, process, submit.

The worker always initiates (plan section 3). If this machine is off, nothing
on the server blocks or times out — batches simply accumulate as rows.

Per ADR 0002 the lease unit is a modality batch, so a model is loaded once
per batch rather than once per video, and results are committed per video so
an interruption costs only the unfinished remainder.

Per ADR 0004 the worker gates on free VRAM before leasing and bails out
mid-batch if memory falls below the critical floor. It never proceeds in a
degraded state.

    python -m gpu_worker.worker --once          # single pass, for testing
    python -m gpu_worker.worker                 # continuous
"""

from __future__ import annotations

import argparse
import os
import platform
import sys
import time
from pathlib import Path

import httpx
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gpu_worker import vram  # noqa: E402
from shared.schemas.jobs import (  # noqa: E402
    BatchLease, Modality, VideoResult, make_idempotency_key,
)

ROOT = Path(__file__).resolve().parent.parent


def load_env() -> dict[str, str]:
    out = {}
    envfile = ROOT / ".env"
    if envfile.exists():
        for line in envfile.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition("=")
                out[k.strip()] = v
    out.update({k: v for k, v in os.environ.items() if k.startswith("VIRALLAB_")})
    return out


def load_config() -> dict:
    return yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(
        encoding="utf-8"))


class Worker:
    def __init__(self, base_url: str, token: str, ca_cert: str | None,
                 config: dict, worker_id: str, dry_run: bool = False):
        self.base_url = base_url.rstrip("/")
        self.config = config
        self.gpu_cfg = config["gpu"]
        self.worker_id = worker_id
        self.dry_run = dry_run
        # Pin the server's self-signed CA rather than disabling verification:
        # the connection crosses a tailnet, and "verify=False" would make the
        # token interceptable by anything on the path.
        verify = ca_cert if ca_cert else True
        self.http = httpx.Client(
            base_url=self.base_url, verify=verify, timeout=60.0,
            headers={"Authorization": f"Bearer {token}"})

    # -- server calls -----------------------------------------------------

    def report(self, state: str, reason: str | None = None,
               free: int | None = None) -> None:
        try:
            self.http.post("/worker/status", json={
                "worker_id": self.worker_id, "state": state, "reason": reason,
                "free_vram_mib": free,
                "total_vram_mib": self.gpu_cfg.get("total_vram_mib")})
        except httpx.HTTPError as exc:
            print(f"  [warn] could not report status: {exc}")

    def lease(self, capabilities: list[str], free: int | None) -> BatchLease | None:
        r = self.http.post("/jobs/lease", json={
            "worker_id": self.worker_id, "capabilities": capabilities,
            "free_vram_mib": free,
            "max_videos": self.config["queue"]["batch_size"].get(
                capabilities[0], 100)})
        r.raise_for_status()
        data = r.json()
        return BatchLease.model_validate(data) if data else None

    def heartbeat(self, lease: BatchLease, done: int, peak: int | None) -> bool:
        r = self.http.post("/jobs/heartbeat", json={
            "batch_id": lease.batch_id, "lease_token": lease.lease_token,
            "worker_id": self.worker_id, "videos_done": done,
            "peak_vram_mib": peak})
        if r.status_code == 409:
            print("  [!] lease no longer ours (409) - abandoning batch")
            return False
        r.raise_for_status()
        return True

    def submit(self, lease: BatchLease, results: list[VideoResult], *,
               final: bool, partial_reason: str | None = None,
               batch_peak: int | None = None) -> bool:
        r = self.http.post("/jobs/results", json={
            "batch_id": lease.batch_id, "lease_token": lease.lease_token,
            "worker_id": self.worker_id,
            "results": [x.model_dump(mode="json") for x in results],
            "final": final, "partial_reason": partial_reason,
            "batch_peak_vram_mib": batch_peak})
        if r.status_code == 409:
            print("  [!] lease no longer ours (409) - results discarded")
            return False
        r.raise_for_status()
        print(f"  committed {r.json()['written']} result(s)"
              f"{' (final)' if final else ''}")
        return True

    # -- processing -------------------------------------------------------

    def process_video(self, lease: BatchLease, ref, sampler) -> VideoResult:
        """Run one video through the leased modality.

        --dry-run exercises the whole protocol without loading a model, which
        is what makes an end-to-end round-trip test possible on a machine
        whose GPU is busy or absent.
        """
        key = make_idempotency_key(lease.modality, ref.video_id,
                                   lease.method_version)
        t0 = time.monotonic()
        if self.dry_run:
            payload = {"dry_run": True, "modality": str(lease.modality),
                       "frames_requested": ref.frames}
        else:
            raise NotImplementedError(
                "real analysis pipelines land in Phase 5; Phase 1 validates "
                "the transport and job protocol")
        peak = max(sampler.samples) if sampler.samples else None
        return VideoResult(
            video_id=ref.video_id, idempotency_key=key, payload=payload,
            compute_seconds=round(time.monotonic() - t0, 3),
            peak_vram_mib=peak)

    def run_batch(self, lease: BatchLease) -> None:
        print(f"  leased batch {lease.batch_id}: {lease.modality} "
              f"tier {lease.tier}, {len(lease.videos)} video(s), "
              f"attempt {lease.attempt}/{lease.max_attempts}")
        sampler = vram.VramSampler()
        sampler.start()
        pending, done = list(lease.videos), []
        last_hb = time.monotonic()
        hb_every = self.config["queue"]["heartbeat_seconds"]
        floor = self.gpu_cfg["critical_floor_mib"]
        bail: str | None = None

        try:
            for ref in pending:
                if not self.dry_run and vram.below_critical_floor(floor):
                    bail = f"free VRAM below critical floor ({floor} MiB)"
                    print(f"  [!] {bail} - committing {len(done)}, "
                          "handing back remainder")
                    break
                done.append(self.process_video(lease, ref, sampler))

                if time.monotonic() - last_hb > hb_every:
                    peak = max(sampler.samples) if sampler.samples else None
                    if not self.heartbeat(lease, len(done), peak):
                        return
                    last_hb = time.monotonic()
                    # Commit progress so a later interruption keeps this work.
                    if not self.submit(lease, done, final=False):
                        return
                    done = []
        finally:
            stats = sampler.stop()

        self.submit(lease, done, final=True, partial_reason=bail,
                    batch_peak=stats["peak_mib"])
        print(f"  batch {lease.batch_id} finished; peak VRAM "
              f"{stats['peak_mib']} MiB over {stats['samples']} samples")

    # -- main loop --------------------------------------------------------

    def tick(self) -> bool:
        """One pass. Returns True if work was done."""
        requirements = self.gpu_cfg["requirements_mib"]
        margin = self.gpu_cfg["safety_margin_mib"]

        allowed, reasons, free_now = [], [], None
        for modality in (Modality.VISION, Modality.TRANSCRIPTION):
            if self.dry_run:
                allowed.append(str(modality))
                continue
            ok, reason, free = vram.check_headroom(
                str(modality), requirements, margin)
            free_now = free or free_now
            (allowed if ok else reasons).append(
                str(modality) if ok else f"{modality}: {reason}")

        if not allowed:
            msg = "; ".join(reasons) or "no modality can run"
            print(f"  deferred - {msg}")
            self.report("deferred", msg, free_now)
            return False

        lease = self.lease(allowed, free_now)
        if lease is None:
            self.report("idle", "no work queued", free_now)
            return False

        self.report("working", f"batch {lease.batch_id}", free_now)
        try:
            self.run_batch(lease)
        finally:
            # Otherwise a finished worker still reads as "working".
            self.report("idle", f"finished batch {lease.batch_id}", free_now)
        return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="exercise the protocol without loading models")
    ap.add_argument("--worker-id",
                    default=f"gpu-{platform.node().lower()}")
    args = ap.parse_args()

    env, config = load_env(), load_config()
    base = env.get("VIRALLAB_SERVER_URL")
    token = env.get("SERVER_WORKER_TOKEN") or env.get("VIRALLAB_WORKER_TOKEN")
    ca = env.get("VIRALLAB_CA_CERT")
    if not base or not token:
        print("VIRALLAB_SERVER_URL and SERVER_WORKER_TOKEN must be set",
              file=sys.stderr)
        return 2
    if ca and not Path(ca).is_file():
        print(f"CA cert not found: {ca}", file=sys.stderr)
        return 2

    w = Worker(base, token, ca, config, args.worker_id, dry_run=args.dry_run)
    print(f"worker {w.worker_id} -> {base} "
          f"(TLS pinned: {bool(ca)}, dry_run: {args.dry_run})")

    if args.once:
        return 0 if w.tick() else 1

    idle_wait = config["gpu"]["poll_interval_seconds"]
    defer_wait = config["gpu"]["backoff_seconds"]
    try:
        while True:
            try:
                did = w.tick()
            except httpx.HTTPError as exc:
                print(f"  [warn] server unreachable: {exc}")
                did = False
            time.sleep(idle_wait if did else defer_wait)
    except KeyboardInterrupt:
        w.report("idle", "worker stopped")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
