"""The credential endpoints are the most dangerous surface in the project.

`PUT /api/credentials/{key}` writes to the server's `.env`. If the key were
not whitelisted, that is an arbitrary environment-variable write reachable
over HTTP, and `PATH`, `LD_PRELOAD`, `PYTHONPATH` and `VIRALLAB_DB_URL` are
all environment variables. These tests exist to keep the whitelist, the
value sanitisation and the write-only property from being refactored away.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from server.api import credentials_api as capi


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    """Point the module at a throwaway .env. Never the real one."""
    path = tmp_path / ".env"
    path.write_text("EXISTING_KEY=keepme\nAPIFY_TOKEN=old\n",
                    encoding="utf-8")
    monkeypatch.setattr(capi, "ENV_PATH", path)
    return path


def test_unmanaged_key_is_refused(env_file, monkeypatch):
    """The whitelist is the control that makes this endpoint acceptable."""
    for dangerous in ("PATH", "LD_PRELOAD", "PYTHONPATH",
                      "VIRALLAB_DB_URL", "SERVER_WORKER_TOKEN"):
        with pytest.raises(Exception) as exc:
            capi.set_credential(dangerous, capi.CredentialWrite(value="x"))
        assert "not a managed credential" in str(exc.value), dangerous
        assert dangerous not in env_file.read_text(encoding="utf-8")


def test_write_then_read_never_returns_the_value(env_file, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "")
    secret = "apify_api_SUPERSECRET_abc123"
    out = capi.set_credential("APIFY_TOKEN",
                              capi.CredentialWrite(value=secret))
    assert out.is_set is True
    assert out.fingerprint == capi.fingerprint(secret)
    # The response model has no value field at all; prove it stays that way.
    assert secret not in out.model_dump_json()


def test_newline_injection_is_refused(env_file):
    """A newline in a value could set an unrelated variable."""
    for payload in ("tok\nPATH=/evil", "tok\rX=1", "tok\x00"):
        with pytest.raises(Exception) as exc:
            capi.set_credential("APIFY_TOKEN",
                               capi.CredentialWrite(value=payload))
        assert "line break" in str(exc.value) or "control" in str(exc.value)
    assert "PATH=/evil" not in env_file.read_text(encoding="utf-8")


def test_empty_value_is_refused(env_file):
    with pytest.raises(Exception) as exc:
        capi.set_credential("APIFY_TOKEN", capi.CredentialWrite(value="  "))
    assert "empty value" in str(exc.value)


def test_oversized_value_is_refused(env_file):
    with pytest.raises(Exception) as exc:
        capi.set_credential(
            "APIFY_TOKEN",
            capi.CredentialWrite(value="a" * (capi.MAX_VALUE_BYTES + 1)))
    assert "implausibly long" in str(exc.value)


def test_other_env_lines_survive_a_write(env_file, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "")
    capi.set_credential("APIFY_TOKEN", capi.CredentialWrite(value="new-token"))
    text = env_file.read_text(encoding="utf-8")
    # The whole point of the atomic rewrite: an interrupted or careless write
    # that lost VIRALLAB_DB_URL would take out the application.
    assert "EXISTING_KEY=keepme" in text
    assert "APIFY_TOKEN=new-token" in text
    assert text.count("APIFY_TOKEN=") == 1


def test_clear_removes_the_line(env_file, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "something")
    out = capi.clear_credential("APIFY_TOKEN")
    assert out.is_set is False
    text = env_file.read_text(encoding="utf-8")
    assert "APIFY_TOKEN" not in text
    assert "EXISTING_KEY=keepme" in text
    assert os.environ.get("APIFY_TOKEN") is None


def test_file_permissions_are_restrictive(env_file, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "")
    capi.set_credential("APIFY_TOKEN", capi.CredentialWrite(value="tok"))
    mode = env_file.stat().st_mode & 0o777
    if os.name != "nt":
        assert mode == 0o600, f"credential file is {oct(mode)}"


def test_spending_requires_explicit_confirmation():
    """A paid dependency must never appear as a side effect."""
    with pytest.raises(Exception) as exc:
        capi.set_spending(capi.SpendingWrite(
            monthly_cap_usd=25.0, providers_authorised=["apify"],
            confirm=False))
    assert "confirm=true" in str(exc.value)


def test_spending_rejects_unknown_provider():
    with pytest.raises(Exception) as exc:
        capi.set_spending(capi.SpendingWrite(
            monthly_cap_usd=5.0, providers_authorised=["definitely-not-real"],
            confirm=True))
    assert "unknown provider" in str(exc.value)


def test_spending_rejects_an_implausible_cap():
    """A typo adding a zero should not become a four-figure authorisation."""
    with pytest.raises(Exception) as exc:
        capi.set_spending(capi.SpendingWrite(
            monthly_cap_usd=50000.0, providers_authorised=["apify"],
            confirm=True))
    assert "refusing" in str(exc.value)


def test_every_slot_is_a_valid_env_name():
    for slot in capi.SLOTS:
        assert capi.KEY_RE.match(slot.key), slot.key
        assert slot.purpose and slot.blocked_without, slot.key


def test_fingerprint_does_not_leak_the_value():
    fp = capi.fingerprint("a-very-secret-token")
    assert len(fp) == 8
    assert "secret" not in fp
