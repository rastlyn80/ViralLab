"""The platform running its own work, and what it may not be talked into.

The owner's objection was that research happened in coding sessions
rather than on the platform. The fix is a button that starts a
collection - which is also a button that starts a process, so the
interesting tests are about what a request cannot cause.

The one that matters: a caller picks an operation by KEY and
supplies NUMBERS. The command line is built in the catalogue. There
is no path from an HTTP body to an argument the catalogue did not
write, and nothing is handed to a shell.
"""

from __future__ import annotations

import inspect

import pytest

from server.ops import catalogue as cat
from server.ops import runner


# ---------------------------------------------------------------------------
# a request cannot choose what runs
# ---------------------------------------------------------------------------

def test_an_unknown_operation_is_refused():
    for attempt in ("rm -rf /", "collect_own; rm -rf /", "", "../../sh"):
        with pytest.raises(KeyError):
            cat.get(attempt)
        with pytest.raises(KeyError):
            cat.argv(attempt, {})


def test_every_command_is_a_list_and_never_a_string():
    """A string would be a shell command. A list is not."""
    for op in cat.CATALOGUE:
        argv = cat.argv(op.key, {})
        assert isinstance(argv, list)
        assert all(isinstance(a, str) for a in argv)
        assert argv, op.key


def test_nothing_reaches_a_shell():
    src = inspect.getsource(runner)
    assert "shell=True" not in src
    assert "shell=False" in src
    assert "os.system" not in src


def test_an_undeclared_parameter_is_dropped():
    """An unexpected field in a request must not become an argument."""
    clean = cat.clean_params("deepen_creators",
                             {"creators": 10, "evil": "; rm -rf /",
                              "max_usd": 2})
    assert set(clean) == {"creators", "per_creator", "max_usd"}
    argv = cat.argv("deepen_creators",
                    {"creators": 10, "evil": "; rm -rf /"})
    assert not any("rm" in a for a in argv)


def test_a_string_parameter_cannot_survive_as_text():
    """Everything the catalogue accepts becomes an int or a default."""
    argv = cat.argv("find_creators", {"limit": "20; cat /etc/passwd"})
    assert "--limit" in argv
    value = argv[argv.index("--limit") + 1]
    assert value.isdigit()


def test_numbers_are_clamped_rather_than_refused():
    """Every bound here is a politeness or spending limit.

    The right answer to "collect 100000" is the maximum, not a
    validation error about a field the owner had no reason to know
    was bounded.
    """
    p = cat.clean_params("watch_competitors", {"cycles": 9999,
                                               "batch": -5})
    assert p["cycles"] == 20
    assert p["batch"] == 5
    assert cat.clean_params("find_creators", {"limit": None})["limit"] == 20


# ---------------------------------------------------------------------------
# what the owner is told before pressing it
# ---------------------------------------------------------------------------

def test_every_operation_explains_itself_without_jargon():
    banned = ("_id", "sql", "api", "json", "schema", "table", "null",
              "idempot", "method_version", "video_features")
    for op in cat.CATALOGUE:
        text = f"{op.title} {op.what_it_does}".lower()
        for word in banned:
            assert word not in text, f"{op.key} says {word!r}"
        assert op.how_long, op.key
        assert len(op.what_it_does) > 40, op.key


def test_anything_that_spends_says_so_and_says_roughly_how_much():
    """A button that silently spends is the worst button here."""
    for op in cat.CATALOGUE:
        if op.costs_money:
            assert op.cost_note, op.key
            assert "$" in op.cost_note, op.key


def test_the_free_ones_are_not_marked_as_costing():
    free = {o.key for o in cat.CATALOGUE if not o.costs_money}
    assert "watch_competitors" in free, (
        "the yt-dlp route is free; ADR 0010 is the whole point")
    assert "read_captions" in free
    assert "read_hooks" in free


def test_a_paid_operation_can_be_capped_by_the_owner():
    for key in ("deepen_creators", "watch_competitors_closely",
                "look_at_my_videos"):
        op = cat.get(key)
        names = {p.key for p in op.params}
        assert names & {"max_usd", "usd_per_round"}, key


# ---------------------------------------------------------------------------
# one at a time
# ---------------------------------------------------------------------------

def test_the_one_live_run_rule_is_enforced_by_the_database():
    """A Python check would race; a partial unique index does not.

    These passes hit other people's servers and paid APIs, so two
    concurrent copies would double the request rate and the spend.
    """
    from server.db.models import Operation

    index = next(i for i in Operation.__table__.indexes
                 if i.name == "uq_operations_one_live_per_kind")
    assert index.unique
    assert "status in ('queued','running')" in str(
        index.dialect_options["postgresql"]["where"])


def test_a_restart_cannot_leave_a_kind_blocked_forever():
    """`reconcile` exists because the follower is a thread.

    Checked by behaviour, not by reading the source - the two tests
    below run it. This one only pins that it is wired into startup,
    because without that one interrupted run blocks its kind for
    ever and the owner is told "already running" about something
    that stopped days ago.
    """
    from server.api import app as app_module
    assert "reconcile" in inspect.getsource(app_module.lifespan)


# ---------------------------------------------------------------------------
# the log cannot fill a disk
# ---------------------------------------------------------------------------

def test_the_log_is_capped():
    big = "x" * (runner.LOG_LIMIT * 3)
    out = runner._tail(big)
    assert len(out) < runner.LOG_LIMIT + 200
    assert "dropped" in out


def test_a_short_log_is_untouched():
    assert runner._tail("hello") == "hello"


# ---------------------------------------------------------------------------
# reading what the pass reported
# ---------------------------------------------------------------------------

def test_the_last_json_object_is_the_summary():
    """Provision scripts print a plan, then a result. The result wins."""
    out = '{"plan": 1}\nsome log\n{"result": "final", "spent_usd": 0.25}'
    assert runner._summarise(out) == {"result": "final",
                                      "spent_usd": 0.25}
    assert runner._cost_of(runner._summarise(out)) == 0.25


def test_a_pass_that_prints_no_json_summarises_to_nothing():
    assert runner._summarise("just some words") is None
    assert runner._cost_of(None) == 0.0


def test_cost_is_found_inside_a_nested_result():
    summary = {"result": {"cost_usd": 0.0, "acquired": 3}}
    assert runner._cost_of(summary) == 0.0
    assert runner._cost_of({"result": {"spent_usd": 1.5}}) == 1.5


def test_progress_is_read_from_the_lines_the_scripts_already_print():
    assert runner._progress_from("=== cycle 3 of 12 ===") == {
        "done": 3, "total": 12, "progress_note": "Round 3 of 12"}
    assert runner._progress_from("INFO 400/2430")["done"] == 400
    assert runner._progress_from("nothing here") is None


def test_reconcile_leaves_a_live_operation_alone(monkeypatch):
    """A healthy two-hour pass must survive a restart of the app.

    The first version closed every live row at startup. A duplicate
    starter that failed to bind the port still ran the lifespan, so
    a collection forty minutes in was marked "Interrupted" by a
    process that never served a request - and that row is the only
    thing the owner sees.
    """
    class Row:
        def __init__(self, pid):
            self.id, self.pid = 1, pid
            self.status, self.error = "running", None
            self.finished_at = self.progress_note = None

    live_row = Row(pid=4242)
    monkeypatch.setattr(runner, "live", lambda s, kind=None: [live_row])
    monkeypatch.setattr(runner, "_alive", lambda pid: True)

    class S:
        def commit(self):
            raise AssertionError("nothing to commit")

    assert runner.reconcile(S()) == 0
    assert live_row.status == "running"
    assert live_row.progress_note is None


def test_reconcile_closes_an_operation_whose_process_is_gone(monkeypatch):
    class Row:
        def __init__(self):
            self.id, self.pid = 2, 999999
            self.status, self.error = "running", None
            self.finished_at = self.progress_note = None

    dead = Row()
    commits = []
    monkeypatch.setattr(runner, "live", lambda s, kind=None: [dead])
    monkeypatch.setattr(runner, "_alive", lambda pid: False)

    class S:
        def commit(self):
            commits.append(1)

    assert runner.reconcile(S()) == 1
    assert dead.status == "failed"
    assert commits == [1]


# ---------------------------------------------------------------------------
# a job must outlive the web app that started it
# ---------------------------------------------------------------------------

def test_output_goes_to_a_file_not_a_pipe():
    """The failure that killed a two-hour collection.

    Output was piped to a thread in the API. A restart took the
    reader away, the pipe filled, and the run died at the write -
    after `reconcile` had already recorded it as interrupted, so
    nothing even looked wrong. A job the owner started must not
    depend on the web app staying up.
    """
    src = inspect.getsource(runner._run)
    assert "subprocess.PIPE" not in src
    assert "stdout=sink" in src


def test_the_child_gets_its_own_session():
    """So a signal aimed at the API does not take the work with it."""
    assert "start_new_session=True" in inspect.getsource(runner._run)


def test_progress_comes_from_reading_the_file():
    """Losing the follower costs the progress bar, not the run."""
    src = inspect.getsource(runner._follow)
    assert "_read(path)" in src
    assert "proc.poll()" in src


def test_the_log_survives_in_a_predictable_place():
    p = runner.log_path(17)
    assert p.name == "17.log"
    assert "operations" in str(p)


def test_a_removed_operation_does_not_leak_its_key_to_the_page():
    """`collect_own` appeared on the page the moment it was deleted.

    Its history rows outlive the catalogue entry, and the title
    fell back to the raw kind - which is the column-name-in-the-
    interface problem arriving through the back door.
    """
    from server.api import ops_api

    class Row:
        id, kind, status = 1, "collect_own", "failed"
        params = {}
        requested_at = started_at = finished_at = None
        progress_note = error = summary = None
        done = total = None
        cost_usd = 0

    title = ops_api._row(Row())["title"]
    assert "_" not in title
    assert "no longer offered" in title
