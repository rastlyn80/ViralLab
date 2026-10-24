"""GPU memory safety, per ADR 0004.

Measured on the RTX 2070 SUPER in Phase 0B: Qwen2.5-VL Q4 peaks at 7634 MiB
of 8192, leaving ~558 MiB against a 1517 MiB desktop baseline. Deferring is
always preferred to thrashing, and silent CPU offload — which completes the
work at a fraction of the measured throughput and so quietly invalidates
every cost projection — is the outcome these checks exist to prevent.
"""

from __future__ import annotations

import statistics
import subprocess
import threading


class VramUnavailable(RuntimeError):
    """nvidia-smi could not be queried. Treated as 'no GPU', never as 'fine'."""


def query_vram() -> tuple[int, int]:
    """Return (used_mib, total_mib) for GPU 0."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VramUnavailable(str(exc)) from exc
    if out.returncode != 0:
        raise VramUnavailable(out.stderr.strip() or "nvidia-smi failed")
    used, total = (int(x) for x in out.stdout.strip().splitlines()[0].split(","))
    return used, total


def free_mib() -> int:
    used, total = query_vram()
    return total - used


def check_headroom(modality: str, requirements: dict[str, int],
                   safety_margin_mib: int) -> tuple[bool, str, int]:
    """Decide whether a batch of this modality may be leased.

    Returns (ok, human_reason, free_mib). The reason is surfaced to the
    dashboard so a deferral reads as a deferral, never as a stall.
    """
    need = requirements.get(modality)
    if need is None:
        return True, f"no VRAM requirement configured for {modality}", 0
    try:
        free = free_mib()
    except VramUnavailable as exc:
        return False, f"cannot query VRAM ({exc}); refusing to lease", 0

    required = need + safety_margin_mib
    if free < required:
        return False, (f"insufficient VRAM: {free} MiB free, need {need} + "
                       f"{safety_margin_mib} margin = {required}"), free
    return True, f"{free} MiB free, need {required}", free


class VramSampler(threading.Thread):
    """Samples VRAM for the duration of a batch so the recorded peak covers
    the whole run rather than one instant. This is the mechanism that
    produced the Phase 0B numbers, so it is already validated."""

    def __init__(self, interval: float = 0.25):
        super().__init__(daemon=True)
        self.interval, self.samples = interval, []
        self._halt = threading.Event()
        self.critical_hit = False

    def run(self) -> None:
        while not self._halt.is_set():
            try:
                used, total = query_vram()
                self.samples.append(used)
            except VramUnavailable:
                pass
            self._halt.wait(self.interval)

    def note_critical(self) -> None:
        self.critical_hit = True

    def stop(self) -> dict:
        self._halt.set()
        self.join(timeout=5)
        if not self.samples:
            return {"peak_mib": None, "mean_mib": None, "samples": 0}
        return {"peak_mib": max(self.samples),
                "mean_mib": round(statistics.fmean(self.samples)),
                "samples": len(self.samples)}


def below_critical_floor(critical_floor_mib: int) -> bool:
    """Mid-batch guard: stop taking new videos, finish the current one, commit
    what is done and hand the remainder back (ADR 0004 section 3)."""
    try:
        return free_mib() < critical_floor_mib
    except VramUnavailable:
        return True    # cannot verify safety -> assume unsafe
