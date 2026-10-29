"""Which creators a deepening run should spend money on.

A run picked a hundred creators, spent $0.62 and moved ten of them
over the line that makes a creator usable at all. Research is
idempotent, so re-selecting creators collected an hour ago costs
nothing and achieves nothing - and the selection was choosing the
same spread across the follower range every time.

The spread is still right: a cohort of megastars would make every
comparison read as "you are not famous". It just has to be applied
to the creators who actually need the work.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import types


def _load():
    """Import the provision script without running it.

    It does `sys.path.insert(0, "/home/rastlyn/virallab")` and
    imports server modules at import time, which is correct on the
    server and useless here, so the path insert is harmless and the
    import is done by spec.
    """
    path = (pathlib.Path(__file__).parents[2] / "scripts" / "provision"
            / "build_cohort.py")
    spec = importlib.util.spec_from_file_location("build_cohort", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build_cohort"] = mod
    spec.loader.exec_module(mod)
    return mod


bc = _load()


def row(handle, followers, pubs):
    return types.SimpleNamespace(handle=handle, platform="tiktok",
                                 follower_count=followers, pubs=pubs)


def test_creators_who_need_the_work_are_chosen_first():
    rows = ([row(f"deep{i}", 10_000 - i, 30) for i in range(20)]
            + [row(f"thin{i}", 5_000 - i, 1) for i in range(20)])
    chosen = bc.pick(rows, 10)
    assert all(c.handle.startswith("thin") for c in chosen), (
        "a creator with 30 videos gains nothing from being collected "
        "again")


def test_the_follower_spread_still_applies_among_them():
    """A cohort of megastars makes every comparison meaningless."""
    thin = [row(f"t{i}", 100_000 - i * 1000, 1) for i in range(100)]
    chosen = bc.pick(thin, 5)
    followers = [c.follower_count for c in chosen]
    assert followers[0] > followers[-1]
    assert followers[0] == 100_000, "the biggest is included"
    assert followers[-1] == thin[-1].follower_count, "so is the smallest"


def test_it_tops_up_from_the_deep_ones_when_thin_ones_run_out():
    rows = ([row(f"thin{i}", 900 - i, 2) for i in range(3)]
            + [row(f"deep{i}", 5_000 - i, 30) for i in range(20)])
    chosen = bc.pick(rows, 8)
    assert len(chosen) == 8
    assert sum(1 for c in chosen if c.handle.startswith("thin")) == 3


def test_the_old_behaviour_is_still_available():
    """A method-version bump wants everyone, not only the thin."""
    rows = [row(f"deep{i}", 1_000 - i, 30) for i in range(10)]
    assert len(bc.pick(rows, 4, thin_first=False)) == 4


def test_asking_for_more_than_exist_returns_what_exists():
    rows = [row("a", 10, 1), row("b", 5, 1)]
    assert len(bc.pick(rows, 50)) == 2


def test_the_threshold_matches_the_analysis():
    from server.research import virality as vir
    assert bc.DEEP_ENOUGH == vir.MIN_PER_CREATOR, (
        "deepening to a different bar than the analysis uses would "
        "collect videos that still cannot be compared")
