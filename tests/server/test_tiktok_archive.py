"""Ownership enforcement for own-account TikTok archival (ADR 0007).

The rejected design let the caller pass a list of "verified" accounts. These
tests encode why it was rejected and keep the replacement honest: ownership
comes from the database and from TikTok's own video list, and nothing a
caller passes can widen it.

No network and no subprocess. `fetch_one` is replaced wherever a test would
otherwise download something, so a failure here can never hit TikTok.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.collectors import tiktok_archive as tta


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    "https://evil.example.com/@handle/video/7312345678901234567",
    "https://tiktok.com.evil.test/@handle/video/7312345678901234567",
    "file:///etc/passwd",
    "ftp://www.tiktok.com/@handle/video/7312345678901234567",
    "https://www.tiktok.com/@handle/photo/7312345678901234567",
    "https://www.tiktok.com/@handle/video/notanumber",
    "https://www.tiktok.com/../../etc/passwd",
    "",
])
def test_hostile_urls_are_rejected_before_any_subprocess(raw):
    target, why = tta.normalise_url(raw, resolve_short=False)
    assert target is None, f"{raw!r} should not have parsed"
    assert why


def test_canonical_url_is_normalised_and_query_dropped():
    target, why = tta.normalise_url(
        "https://www.tiktok.com/@gabrielebaldocci/video/7699534488434527510"
        "?utm_campaign=tt4d_open_api&utm_source=secret",
        resolve_short=False)
    assert target is not None, why
    assert target.handle == "gabrielebaldocci"
    assert target.video_id == "7699534488434527510"
    # The tracking parameters carry an API-side identifier; they are not
    # needed to fetch and are not kept.
    assert "utm_source" not in target.url
    assert target.url == ("https://www.tiktok.com/@gabrielebaldocci"
                          "/video/7699534488434527510")


# ---------------------------------------------------------------------------
# ownership
# ---------------------------------------------------------------------------

class FakeAdapter:
    """Stands in for the official adapter's video list."""

    def __init__(self, by_account: dict[str, list[str]]):
        self.by_account = by_account
        self.calls: list[str] = []

    def _iter_raw(self, account_ref):
        self.calls.append(account_ref)
        for vid in self.by_account.get(account_ref, []):
            yield {"id": vid,
                   "share_url": f"https://www.tiktok.com/@owner/video/{vid}"}


def test_oauth_video_index_uses_the_platforms_own_share_url():
    adapter = FakeAdapter({"acct": ["111111", "222222"]})
    index = tta.oauth_video_index(adapter, "acct")
    assert set(index) == {"111111", "222222"}
    assert index["111111"].endswith("/video/111111")


def test_ownership_of_is_case_insensitive_and_strips_at():
    profiles = [tta.OwnedProfile(account_ref="X", handle="owner",
                                ownership_basis="oauth")]
    assert tta.ownership_of("@OWNER", profiles) is not None
    assert tta.ownership_of("owner", profiles) is not None
    assert tta.ownership_of("someone_else", profiles) is None


def test_operator_marked_profile_is_not_strong():
    weak = tta.OwnedProfile(account_ref="X", handle="h",
                            ownership_basis="operator_marked")
    strong = tta.OwnedProfile(account_ref="X", handle="h",
                              ownership_basis="oauth")
    assert weak.strong is False
    assert strong.strong is True


# ---------------------------------------------------------------------------
# the private path
# ---------------------------------------------------------------------------

def test_private_sync_is_off_unless_explicitly_enabled(monkeypatch):
    monkeypatch.delenv(tta.PRIVATE_SYNC_ENV, raising=False)
    assert tta.private_sync_enabled() is False
    target = tta.UrlTarget(url="u", handle="owner", video_id="111111")
    auth = tta.authorise_private_sync(None, target, adapter=None, profiles=[])
    assert auth.allowed is False
    assert "disabled" in auth.reason


def test_private_sync_refuses_a_profile_without_oauth(monkeypatch):
    monkeypatch.setenv(tta.PRIVATE_SYNC_ENV, "1")
    profiles = [tta.OwnedProfile(account_ref="X", handle="owner",
                                ownership_basis="operator_marked")]
    target = tta.UrlTarget(url="u", handle="owner", video_id="111111")
    auth = tta.authorise_private_sync(None, target, adapter=FakeAdapter({}),
                                      profiles=profiles)
    assert auth.allowed is False
    assert "no TikTok OAuth token" in auth.reason


def test_private_sync_refuses_a_video_tiktok_does_not_list(monkeypatch):
    """The gate that actually binds.

    A cookie jar proves nothing about which account it belongs to, so the
    OAuth video list - the platform's own statement of ownership - is what
    decides. A video absent from it is refused however many credentials are
    offered.
    """
    monkeypatch.setenv(tta.PRIVATE_SYNC_ENV, "1")
    profiles = [tta.OwnedProfile(account_ref="acct", handle="owner",
                                ownership_basis="oauth")]
    adapter = FakeAdapter({"acct": ["111111"]})
    target = tta.UrlTarget(url="u", handle="owner", video_id="999999")
    auth = tta.authorise_private_sync(None, target, adapter=adapter,
                                      profiles=profiles)
    assert auth.allowed is False
    assert "does not list" in auth.reason or "not in the" in auth.reason


def test_private_sync_allows_a_video_tiktok_confirms(monkeypatch):
    monkeypatch.setenv(tta.PRIVATE_SYNC_ENV, "1")
    profiles = [tta.OwnedProfile(account_ref="acct", handle="owner",
                                ownership_basis="oauth")]
    adapter = FakeAdapter({"acct": ["111111"]})
    target = tta.UrlTarget(url="u", handle="owner", video_id="111111")
    auth = tta.authorise_private_sync(None, target, adapter=adapter,
                                      profiles=profiles)
    assert auth.allowed is True
    assert auth.confirmed_by_platform is True


def test_private_sync_refuses_when_the_list_cannot_be_read(monkeypatch):
    """Unreachable API must mean 'refuse', never 'assume yes'."""
    monkeypatch.setenv(tta.PRIVATE_SYNC_ENV, "1")

    class Broken:
        def _iter_raw(self, account_ref):
            raise RuntimeError("tiktok unreachable")
            yield  # pragma: no cover

    profiles = [tta.OwnedProfile(account_ref="acct", handle="owner",
                                ownership_basis="oauth")]
    target = tta.UrlTarget(url="u", handle="owner", video_id="111111")
    auth = tta.authorise_private_sync(None, target, adapter=Broken(),
                                      profiles=profiles)
    assert auth.allowed is False
    assert "could not be confirmed" in auth.reason


def test_private_sync_needs_a_token_too(monkeypatch):
    """A reauth-flagged token cannot confirm anything."""
    monkeypatch.setenv(tta.PRIVATE_SYNC_ENV, "1")
    profiles = [tta.OwnedProfile(account_ref="acct", handle="owner",
                                ownership_basis="oauth",
                                token_needs_reauth=True)]
    target = tta.UrlTarget(url="u", handle="owner", video_id="111111")
    auth = tta.authorise_private_sync(None, target, adapter=FakeAdapter({}),
                                      profiles=profiles)
    assert auth.allowed is False
    assert "re-authorisation" in auth.reason


# ---------------------------------------------------------------------------
# the public path carries no credentials
# ---------------------------------------------------------------------------

def test_public_fetch_passes_no_cookies(monkeypatch):
    """A public fetch must hold nothing it could leak."""
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        raise AssertionError("stop before downloading")

    monkeypatch.setattr(tta.shutil, "which", lambda name: "/usr/bin/yt-dlp")
    monkeypatch.setattr(tta, "tool_available", lambda: (True, "test"))
    monkeypatch.setattr(tta.subprocess, "run", fake_run)

    target = tta.UrlTarget(url="https://www.tiktok.com/@o/video/111111",
                           handle="o", video_id="111111")
    out = tta.fetch_one(target, Path("/tmp/does-not-matter.mp4"))
    cmd = seen["cmd"]
    assert "--no-cookies" in cmd
    assert not any(a.startswith("--username") for a in cmd)
    assert not any("cookies-from-browser" in str(a) for a in cmd)
    assert "--netrc" not in cmd
    assert out.status == tta.SyncStatus.FAILED


def test_private_fetch_passes_the_jar_by_path(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        raise AssertionError("stop before downloading")

    monkeypatch.setattr(tta.shutil, "which", lambda name: "/usr/bin/yt-dlp")
    monkeypatch.setattr(tta, "tool_available", lambda: (True, "test"))
    monkeypatch.setattr(tta.subprocess, "run", fake_run)

    target = tta.UrlTarget(url="https://www.tiktok.com/@o/video/111111",
                           handle="o", video_id="111111")
    jar = Path("/tmp/jar.txt")
    tta.fetch_one(target, Path("/tmp/x.mp4"), cookies=jar)
    cmd = [str(a) for a in seen["cmd"]]
    assert "--cookies" in cmd
    # str(Path(...)) differs by platform, so compare the same way the code
    # builds it rather than hard-coding a POSIX separator.
    assert str(jar) in cmd
    # The jar is passed by path, never inline on the command line, so it
    # does not appear in the process table.
    assert not any("# Netscape" in str(a) for a in cmd)
    assert "--no-cookies" not in cmd


# ---------------------------------------------------------------------------
# failure classification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stderr,expected", [
    ("ERROR: HTTP Error 429: Too Many Requests", tta.SyncStatus.RATE_LIMITED),
    ("ERROR: This post is private", tta.SyncStatus.PRIVATE),
    ("ERROR: Video not available in your country",
     tta.SyncStatus.GEO_BLOCKED),
    ("ERROR: Video unavailable", tta.SyncStatus.UNAVAILABLE),
    ("ERROR: Unable to extract video data", tta.SyncStatus.FAILED),
])
def test_errors_are_classified_for_the_dashboard(stderr, expected):
    status, detail = tta._classify(stderr, 1)
    assert status == expected
    assert detail


def test_transient_and_terminal_do_not_overlap():
    """Retry policy depends on these being disjoint."""
    assert not (tta.SyncStatus.TRANSIENT & tta.SyncStatus.TERMINAL)


def test_cookie_jar_is_shredded(tmp_path):
    jar = tmp_path / "jar.txt"
    jar.write_text("# Netscape HTTP Cookie File\nsecret\n", encoding="utf-8")
    tta._shred(jar)
    assert not jar.exists()
