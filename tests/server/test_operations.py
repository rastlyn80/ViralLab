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

import datetime as dt
import inspect
import re

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
            # Set, because this one really did start. A row
            # without it never reached the runner at all and is
            # deleted rather than closed.
            self.started_at = dt.datetime(2026, 11, 3, 12, 0)

    live_row = Row(pid=4242)
    monkeypatch.setattr(runner, "live", lambda s, kind=None: [live_row])
    monkeypatch.setattr(runner, "_alive", lambda pid: True)
    monkeypatch.setattr(runner, "_sweep_phantoms", lambda s: 0)

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
            self.started_at = dt.datetime(2026, 11, 3, 12, 0)

    dead = Row()
    commits = []
    monkeypatch.setattr(runner, "live", lambda s, kind=None: [dead])
    monkeypatch.setattr(runner, "_alive", lambda pid: False)
    monkeypatch.setattr(runner, "_sweep_phantoms", lambda s: 0)

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


# ---------------------------------------------------------------------------
# the first parameter that is not a number
# ---------------------------------------------------------------------------

def test_a_handle_can_be_pasted_in_any_of_the_ways_people_paste_one():
    """He will paste "@name", a profile link, or type the handle.
    Three error messages for one intention is bad manners."""
    for raw, want in [
        ("@Some.Pianist", "Some.Pianist"),
        ("https://www.tiktok.com/@emiliopiano?lang=en", "emiliopiano"),
        ("https://www.instagram.com/someone/", "someone"),
        ("plainhandle", "plainhandle"),
    ]:
        argv = cat.argv("follow_creator", {"handle": raw})
        assert argv[argv.index("--handle") + 1] == want, raw


@pytest.mark.parametrize("bad", [
    "; rm -rf /", "", "a", "x" * 40, "has space", "../../etc/passwd",
    "/etc/passwd", "name;other", "$(whoami)", "a&b",
])
def test_anything_that_is_not_a_handle_is_refused_not_repaired(bad):
    """Numbers are clamped because "collect 100000" has an obvious
    right answer. A handle does not: the nearest valid one is not
    what he meant, and a value quietly reinterpreted is the bug
    this type exists to prevent.

    `../../etc/passwd` is in this list because the first version
    returned `passwd` - it took the last path segment of anything
    with a slash in it. Nothing bad followed, since it reached a
    creator lookup and failed, but it is exactly the quiet
    reinterpretation the class was written against.
    """
    with pytest.raises(ValueError):
        cat.argv("follow_creator", {"handle": bad})


def test_a_words_pattern_must_be_anchored_at_both_ends():
    """`re.match` anchors the start only, so a pattern without `$`
    would accept anything BEGINNING with a valid handle - which
    fences nothing. Checked at import for the whole catalogue;
    checked here for the check."""
    loose = cat.Words("h", "H", pattern=r"^[a-z]+")
    op = cat.Operation(key="x", title="x", what_it_does="x",
                       how_long="x", build=lambda p: [],
                       params=(loose,))
    original = cat.CATALOGUE
    try:
        cat.CATALOGUE = original + (op,)
        with pytest.raises(ValueError, match="anchored at both ends"):
            cat._validate()
    finally:
        cat.CATALOGUE = original
    cat._validate()                      # the real one still passes


def test_a_choice_falls_back_rather_than_raising():
    """A platform that is not one of ours is a bug in our own
    page, not something he typed. Falling back to the default
    beats showing him a validation error about a dropdown."""
    argv = cat.argv("follow_creator",
                    {"handle": "someone", "platform": "myspace"})
    assert argv[argv.index("--platform") + 1] == "tiktok"


def test_the_page_is_told_how_to_draw_every_parameter():
    """`kind` is derived from the type rather than declared, so a
    new parameter cannot reach the interface without the
    interface knowing what control to render."""
    for op in cat.CATALOGUE:
        for p in cat.as_dict(op)["params"]:
            assert p["kind"] in ("number", "text", "choice"), p
            if p["kind"] == "number":
                assert "minimum" in p and "maximum" in p
            if p["kind"] == "choice":
                assert p["options"]


def test_every_operation_points_at_a_script_that_exists():
    """A button whose script is missing fails when it is pressed,
    on a machine where the file plainly exists locally - which is
    how `collect_own` spent weeks exiting 2.

    `scripts/provision` was not in the deploy at all until
    2026-11-03; scripts were uploaded by hand, one at a time. It
    is now, and `deploy.py` refuses to sync while this is false.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    src = (root / "server" / "ops" / "catalogue.py").read_text(
        encoding="utf-8")
    names = set(re.findall(r'_script\("([^"]+)"\)', src))
    assert names, "no operation runs a script; the pattern has drifted"
    for name in sorted(names):
        assert (root / "scripts" / "provision" / name).exists(), name


def test_the_request_model_accepts_the_parameters_the_catalogue_declares():
    """It was `dict[str, int]`, which was right while every
    parameter was a bounded integer and silently wrong the moment
    one was not. The first handle came back as a Pydantic
    `int_parsing` error naming a field the owner never saw, and
    the button could not have worked for any input.

    Asserted against the catalogue rather than against a literal,
    so a future string parameter is covered the day it is added.
    """
    from server.api.ops_api import Run

    for op in cat.CATALOGUE:
        params = {}
        for p in cat.as_dict(op)["params"]:
            params[p["key"]] = (p["default"] if p["kind"] != "number"
                                else p["default"])
        parsed = Run(kind=op.key, params=params)
        assert set(parsed.params) == set(params), op.key


def test_the_request_model_refuses_a_value_too_long_to_be_a_parameter():
    """The only validation at this layer. What a value may BE
    belongs to the catalogue, which owns the pattern; two places
    that both half-validate is how one of them drifts."""
    import pydantic

    from server.api.ops_api import Run

    with pytest.raises(pydantic.ValidationError):
        Run(kind="follow_creator", params={"handle": "x" * 500})


# ---------------------------------------------------------------------------
# what the evidence is short of
# ---------------------------------------------------------------------------

def test_the_threshold_comes_from_the_analysis_not_a_copy():
    """The page says a creator under N posts is in the database
    and in no finding. If the page's N and the analysis's N ever
    differ, the page is lying about which creators count, and a
    restated constant is how that happens."""
    from server.ops import what_is_missing as wim
    from server.research import virality as vir

    assert wim.MIN_PER_CREATOR is vir.MIN_PER_CREATOR


def test_every_suggested_job_names_a_real_button():
    """A "do this next" that points at an operation the catalogue
    does not have is worse than no suggestion."""
    from server.ops import what_is_missing as wim

    class _Dead:
        def execute(self, *_a, **_k):
            raise RuntimeError("no database here")

        def scalar(self, *_a, **_k):
            raise RuntimeError("no database here")

    # With no database it returns {} rather than raising, so the
    # Collect page still renders its buttons.
    assert wim.summary(_Dead()) == {}

    import inspect
    src = inspect.getsource(wim.summary)
    runs = set(re.findall(r'"run": "([a-z_]+)"', src))
    assert runs
    for key in runs:
        cat.get(key)                     # KeyError if it is not real


# ---------------------------------------------------------------------------
# the 8,181 rows for work that never ran
# ---------------------------------------------------------------------------

def test_the_schedule_waits_before_its_first_look():
    """A server that cannot bind its port still runs the lifespan.

    `virallab-api.service` restarts every ten seconds on failure,
    and a second copy of the server had been started by hand, so
    systemd's copy could never bind. uvicorn runs the application
    lifespan BEFORE binding, so each doomed process started this
    schedule, ticked at once, queued a `read_repertoire`, and died
    about twelve seconds later. 24,255 times, leaving 7,141 rows
    of work nothing would ever run — and the owner found it as a
    button greyed out saying "Already running".

    A settling delay longer than a doomed process lives is the
    cheap half of the fix. `restart_api.py` going through systemd
    is the other half. Neither alone is enough.
    """
    from server.ops import schedule as sched

    assert sched.SETTLE_SECONDS >= 60
    src = inspect.getsource(sched.loop)
    assert "await asyncio.sleep(settle_seconds)" in src
    # Before the loop, not inside it.
    assert src.index("sleep(settle_seconds)") < src.index("while True")


def test_the_restart_script_goes_through_systemd():
    """Two ways to start a server is how this happened. The manual
    path survives only for a machine with no unit, and says so."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "scripts" / "provision"
           / "restart_api.py").read_text(encoding="utf-8")
    assert "systemctl restart" in src
    assert "def unit_exists" in src
    # And it notices the loop rather than measuring one healthy
    # second: a unit restarting every ten seconds looks active.
    assert "NRestarts" in src


def test_a_run_that_never_started_is_removed_not_marked_failed():
    """`started_at` is the first thing the runner writes, before
    the subprocess exists. A row without it has no log, no exit
    code and no work — calling it "the app restarted while this
    was running" is wrong twice: nothing was running, and the row
    buries the runs that did happen."""
    src = inspect.getsource(runner.reconcile)
    assert "row.started_at is None and row.pid is None" in src
    assert "session.delete(row)" in src
    sweep = inspect.getsource(runner._sweep_phantoms)
    assert "Operation.started_at.is_(None)" in sweep
    assert "Operation.pid.is_(None)" in sweep
    # Only rows already judged failed. A live queued row belongs
    # to a thread that may be about to write `started_at`.
    assert 'Operation.status == "failed"' in sweep


def test_reconcile_deletes_a_live_row_that_never_reached_the_runner(
        monkeypatch):
    """The 7,141. `start()` commits the row, then the thread writes
    `started_at`. A process that died between the two leaves a row
    for work that has no log, no exit code and no process — and
    "the app restarted while this was running" is wrong twice
    over, because nothing was running."""
    class Row:
        def __init__(self):
            self.id, self.pid = 3, None
            self.status, self.error = "queued", None
            self.finished_at = self.progress_note = None
            self.started_at = None

    phantom = Row()
    deleted, commits = [], []
    monkeypatch.setattr(runner, "live", lambda s, kind=None: [phantom])
    monkeypatch.setattr(runner, "_alive", lambda pid: False)
    monkeypatch.setattr(runner, "_sweep_phantoms", lambda s: 0)

    class S:
        def delete(self, row):
            deleted.append(row)

        def commit(self):
            commits.append(1)

    # Nothing was "closed" — it is not history, so it is not
    # counted as an interrupted run either.
    assert runner.reconcile(S()) == 0
    assert deleted == [phantom]
    assert commits == [1]
    assert phantom.status == "queued"      # not rewritten on the way out


# ---------------------------------------------------------------------------
# an exhausted provider is not a bad handle
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status,body", [
    (403, '{"error":{"type":"platform-feature-disabled",'
          '"message":"Monthly usage hard limit exceeded"}}'),
    (402, "monthly usage hard limit"),
    (403, "MONTHLY-USAGE-HARD-LIMIT"),
])
def test_an_exhausted_account_is_recognised(status, body):
    """Observed live 2026-11-03: Apify answered the owner's first
    use of "Study a creator you name" with this, and the product
    told him the handle might be misspelled or the account
    private. Both wrong, and the advice sent him looking in the
    one place the fault was not."""
    from server.discovery.apify import _is_out_of_credit

    assert _is_out_of_credit(status, body)


@pytest.mark.parametrize("status,body", [
    (404, "actor not found"),
    (403, "forbidden: bad token"),
    (400, "monthly usage hard limit"),     # wrong status, not this
    (500, "monthly usage hard limit"),
])
def test_other_refusals_are_not_mistaken_for_it(status, body):
    """Over-matching would hide a genuinely bad handle behind
    "the provider is out of credit", which is the same mistake in
    the other direction."""
    from server.discovery.apify import _is_out_of_credit

    assert not _is_out_of_credit(status, body)


def test_running_out_of_credit_is_its_own_kind_of_error():
    """A bad handle is about ONE query: skip it and carry on. An
    exhausted account is about EVERY query: stop at once. The same
    morning this was found, a cohort pass made the identical
    refused call 51 times because nothing told the loop to stop.
    """
    from server.discovery.base import DiscoveryError, ProviderOutOfCredit

    err = ProviderOutOfCredit("out", provider="apify")
    assert isinstance(err, DiscoveryError)
    assert err.permanent is True
    assert err.provider == "apify"


def test_the_handle_advice_is_withheld_when_the_handle_is_not_the_fault():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "scripts" / "provision"
           / "follow_creator.py").read_text(encoding="utf-8")
    assert "hard limit" in src
    assert "limit on your Apify account" in src
    # And it asks before it tries, so the common case never gets
    # as far as a confusing failure.
    assert "adapter.allowance()" in src
    assert src.index("adapter.allowance()") < src.index("research_creator(s")


def test_the_allowance_check_never_raises():
    """Not knowing the allowance is not a reason to refuse to
    work, so a provider that cannot be asked is reported as
    unknown and the buttons stay."""
    import inspect

    from server.discovery.apify import ApifyDiscoveryAdapter
    from server.ops import what_is_missing as wim

    src = inspect.getsource(ApifyDiscoveryAdapter.allowance)
    assert '"known": False' in src
    assert "except Exception" in src
    assert "except Exception" in inspect.getsource(wim.provider_allowance)


def test_every_paid_script_asks_the_provider_before_it_spends():
    """One of three had the check, which is the same as none: the
    owner does not know which button goes through which script.

    `follow_creator` got it when the fault was found. The other
    two are the ones that failed earlier the same day — and
    `build_cohort` is the loop that made the identical refused
    call 51 times, because a permanent refusal about the ACCOUNT
    was indistinguishable from one about a handle.
    """
    from pathlib import Path

    provision = Path(__file__).resolve().parents[2] / "scripts" / "provision"
    for name in ("follow_creator.py", "build_cohort.py",
                 "discover_niche.py"):
        src = (provision / name).read_text(encoding="utf-8")
        assert "allowance()" in src, name
        assert "provider_out_of_credit" in src, name


def test_the_cohort_loop_stops_rather_than_trying_the_next_creator():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "scripts" / "provision"
           / "build_cohort.py").read_text(encoding="utf-8")
    at = src.index("except ProviderOutOfCredit")
    # `break`, not `continue`: every remaining creator would get
    # the identical refusal.
    assert "break" in src[at:at + 700]
    assert "continue" not in src[at:at + 700]
