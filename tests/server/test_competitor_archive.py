"""Fetching other creators' public video, and the lines it may not cross.

ADR 0010 widened CLAUDE.md §7 to allow `yt-dlp` on public
third-party video. The widening is only defensible if its conditions
are properties of the code rather than of how someone calls it, so
these tests check the conditions, not the download.

The one that matters most is the first: there is no way to supply a
credential. `tiktok_archive.fetch_one` accepts a cookie jar for the
owner's own private videos; if this module could pass one through,
the ADR would be describing a general-purpose authenticated scraper
and the distinction it rests on would be a comment.
"""

from __future__ import annotations

import inspect

from server.collectors import competitor_archive as ca
from server.collectors import tiktok_archive as ta


# ---------------------------------------------------------------------------
# no credential can reach yt-dlp
# ---------------------------------------------------------------------------

def test_no_function_here_accepts_a_credential():
    """The control is the absence of the argument.

    `session` is deliberately not in the banned list: it is a
    SQLAlchemy session, which is the opposite kind of thing.
    """
    banned = ("cookie", "cookies", "cookiejar", "credential", "token",
              "username", "password", "netrc", "auth")
    for name, fn in vars(ca).items():
        if not inspect.isfunction(fn) or fn.__module__ != ca.__name__:
            continue
        params = set(inspect.signature(fn).parameters)
        assert not params & set(banned), (
            f"{name} takes a credential-shaped argument: "
            f"{params & set(banned)}")


def test_the_fetch_is_called_without_cookies(monkeypatch):
    """Checked by watching the call, not by reading the source.

    The first version of this test grepped `acquire_one` for the
    word "cookies" and failed on the comment explaining why there
    are none - the same lesson as the guard tests in
    `test_virality_guards`, learned twice in one day.
    """
    seen = {}

    def spy(target, dest, **kwargs):
        seen["kwargs"] = kwargs
        return ta.FetchOutcome(status=ta.SyncStatus.FAILED)

    monkeypatch.setattr(ca, "fetch_one", spy)
    monkeypatch.setattr(
        ca.permalink, "for_video",
        lambda v, c: ("https://www.tiktok.com/@pianist_example"
                      "/video/7300000000000000001"))

    class FakeVideo:
        id = 1

    assert ca.acquire_one(None, FakeVideo(), None) == ta.SyncStatus.FAILED
    assert "cookies" not in seen["kwargs"], (
        "acquire_one must never pass a cookie jar")


def test_the_fetch_default_is_load_bearing():
    """If `cookies` ever gains a non-None default, every fetch here
    becomes authenticated without this module changing a line."""
    assert inspect.signature(ta.fetch_one).parameters["cookies"].default \
        is None


def test_the_private_path_is_not_imported():
    """Nothing authenticated is even in scope in this module."""
    src = inspect.getsource(ca)
    for forbidden in ("fetch_private_owned", "authorise_private_sync"):
        assert forbidden not in src


# ---------------------------------------------------------------------------
# politeness, and stopping when told to
# ---------------------------------------------------------------------------

def test_it_waits_longer_than_the_own_account_path():
    """Somebody else's catalogue, and no deadline on any of it."""
    assert ca.MIN_SECONDS_BETWEEN >= ta.MIN_SECONDS_BETWEEN
    assert ca.DEFAULT_MAX_PER_RUN <= 100


def test_being_rate_limited_stops_the_whole_run(monkeypatch):
    """Continuing to knock after being told to slow down is the
    thing §2.3 forbids in spirit, whatever a retry counter says."""
    seen = []

    class FakeVideo:
        id = 1

    monkeypatch.setattr(ca, "tool_available", lambda: (True, ""))
    monkeypatch.setattr(ca, "candidates",
                        lambda *a, **k: [(FakeVideo(), None)] * 5)
    monkeypatch.setattr(ca, "acquire_one",
                        lambda *a, **k: ca.SyncStatus.RATE_LIMITED)
    res = ca.acquire(None, max_videos=5, sleeper=seen.append)

    assert res.attempted == 1, "stopped after the first refusal"
    assert res.rate_limited == 1
    assert "rate-limited" in res.stopped_reason


def test_a_missing_tool_stops_before_anything_is_attempted(monkeypatch):
    monkeypatch.setattr(ca, "tool_available", lambda: (False, "no yt-dlp"))
    res = ca.acquire(None)
    assert res.attempted == 0
    assert res.stopped_reason == "no yt-dlp"


def test_it_pauses_between_fetches_but_not_before_the_first(monkeypatch):
    waits = []

    class FakeVideo:
        id = 1

    monkeypatch.setattr(ca, "tool_available", lambda: (True, ""))
    monkeypatch.setattr(ca, "candidates",
                        lambda *a, **k: [(FakeVideo(), None)] * 3)
    monkeypatch.setattr(ca, "acquire_one",
                        lambda *a, **k: ca.SyncStatus.UNAVAILABLE)
    ca.acquire(None, max_videos=3, pause_s=7.0, sleeper=waits.append)
    assert waits == [7.0, 7.0]


# ---------------------------------------------------------------------------
# nothing is kept
# ---------------------------------------------------------------------------

def test_everything_fetched_is_registered_as_transient():
    """An unregistered file has no deadline and no reaper."""
    src = inspect.getsource(ca.acquire_one)
    assert "transient.register" in src
    # And if registration fails the bytes go, rather than sitting on
    # disk invisible to the sweep.
    assert "unlink" in src


def test_the_permission_basis_is_recorded_on_every_file():
    assert "ADR 0010" in ca.PERMISSION_BASIS
    assert "public" in ca.PERMISSION_BASIS.lower()
    assert ca.SOURCE != ""


def test_it_reports_costing_nothing():
    """The whole point of the route the owner chose."""
    assert ca.AcquireResult().as_dict()["cost_usd"] == 0.0


def test_a_rejected_url_never_reaches_a_subprocess(monkeypatch):
    """`normalise_url` returns (target, reason), not a target.

    Forgetting to unpack it handed a tuple straight to the download
    layer, which failed deep inside `fetch_one` on the first live
    run rather than being refused here.
    """
    called = []
    monkeypatch.setattr(ca, "fetch_one",
                        lambda *a, **k: called.append(a))
    monkeypatch.setattr(ca.permalink, "for_video",
                        lambda v, c: "https://example.invalid/not-tiktok")

    class FakeVideo:
        id = 1

    assert ca.acquire_one(None, FakeVideo(), None) == ca.SyncStatus.BAD_URL
    assert called == [], "a rejected URL must not be downloaded"


def test_a_video_with_no_derivable_url_is_unavailable(monkeypatch):
    monkeypatch.setattr(ca.permalink, "for_video", lambda v, c: None)

    class FakeVideo:
        id = 1

    assert ca.acquire_one(None, FakeVideo(), None) == ca.SyncStatus.UNAVAILABLE


def test_an_already_analysed_publication_is_not_fetched_again():
    """The bug that cost twelve cycles and nothing to show for it.

    `candidates` first excluded only publications currently HELD,
    and a file stops being held the moment the deterministic pass
    releases it. So a twelve-cycle run downloaded the same forty
    videos twelve times and moved coverage by nothing after the
    first. Against someone else's servers that is the opposite of
    the rate limiting this whole route is conditioned on.
    """
    import inspect as _inspect
    src = _inspect.getsource(ca.candidates)
    assert "analysed_ids" in src
    # And re-fetching stays possible, for a method-version bump.
    assert "redo" in _inspect.signature(ca.candidates).parameters
    assert "redo" in _inspect.signature(ca.acquire).parameters


def test_what_counts_as_analysed_is_not_restated_here():
    """One definition, shared with the thing that reports coverage.

    Two copies of the stage list would let the fetcher and the
    coverage report disagree about which publications are done,
    which is the same class of bug one layer up.
    """
    src = inspect.getsource(ca.analysed_ids)
    assert "from server.features.competitor import STAGES" in src
    for version in ("hook-background-v1", "hook-ocr-v1"):
        assert version not in inspect.getsource(ca), (
            f"{version} is hardcoded; read it from STAGES instead")
