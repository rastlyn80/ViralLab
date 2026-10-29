"""The short memory in front of the analyses.

`virality.build` and `breakthroughs` read every feature row and run
a few hundred rank tests. One page asks for the same numbers four
times - Home through `next_steps`, the hub directly, "Am I doing
it?" through `adherence`, and the creative brief - so each page was
paying for the work four times over.

The risk of any cache is returning the right answer to the wrong
question, so that is what these test.
"""

from __future__ import annotations

import inspect
import os

from server.research import memo


def setup_function():
    memo.clear()


def test_the_work_happens_once():
    calls = []
    out = [memo.remember("t", lambda: calls.append(1) or "v")
           for _ in range(3)]
    assert calls == [1]
    assert out == ["v", "v", "v"]


def test_different_arguments_are_different_answers():
    """The bug that would matter: the owner's findings for a
    competitor query. Every argument is part of the key."""
    mine = memo.remember("virality.build", lambda: "mine", own=True)
    theirs = memo.remember("virality.build", lambda: "theirs", own=False)
    assert mine == "mine"
    assert theirs == "theirs"
    assert memo.remember("virality.build", lambda: "x", own=True) == "mine"


def test_the_origin_split_is_part_of_the_key():
    a = memo.remember("b", lambda: "rep", own=True, origin="repertoire")
    b = memo.remember("b", lambda: "own", own=True,
                      origin="own_composition")
    assert a != b


def test_the_database_is_part_of_the_key(monkeypatch):
    """Tests point the app at a disposable database at runtime, and
    a memo shared across them would hand one database's findings to
    another."""
    monkeypatch.setenv("VIRALLAB_DB_URL", "postgresql://one")
    first = memo.remember("k", lambda: "one")
    monkeypatch.setenv("VIRALLAB_DB_URL", "postgresql://two")
    second = memo.remember("k", lambda: "two")
    assert first == "one"
    assert second == "two"


def test_clearing_forgets_everything():
    memo.remember("k", lambda: "first")
    assert memo.clear() >= 1
    assert memo.remember("k", lambda: "second") == "second"


def test_it_expires_on_its_own():
    """The TTL is the safety net, so it has to be short enough that
    a stale number is never interesting."""
    assert 0 < memo.TTL_SECONDS <= 300


def test_an_operation_finishing_clears_it():
    """The only moment the underlying rows change in normal use."""
    from server.ops import runner

    src = inspect.getsource(runner._run)
    assert "memo.clear()" in src


def test_the_heavy_analyses_go_through_it():
    from server.research import virality as vir

    for fn in (vir.build, vir.breakthroughs):
        assert "memo.remember" in inspect.getsource(fn), fn.__name__
