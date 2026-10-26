"""Hard guard: tests must never touch the application database.

The database tests TRUNCATE every table they use. Pointed at the real
database — which is exactly what happened before this guard existed, since
both read `VIRALLAB_DB_URL` — a single `pytest` run would destroy the
collected history. That history is partly unrecoverable: Instagram retains
insights for only ~2 years, so a wiped backfill cannot be fully re-collected.

The rules, enforced at collection time rather than left to discipline:

  1. Tests read **`VIRALLAB_TEST_DB_URL`**, never `VIRALLAB_DB_URL`.
  2. The test database name must end in `_test`.
  3. If it resolves to the same database as `VIRALLAB_DB_URL`, abort.

Failing any of these aborts the whole run before a single table is touched.
"""

from __future__ import annotations

import os

import pytest

TEST_DB_ENV = "VIRALLAB_TEST_DB_URL"
APP_DB_ENV = "VIRALLAB_DB_URL"
REQUIRED_SUFFIX = "_test"


def _dbname(url: str) -> str:
    """Database name from a SQLAlchemy URL, without parsing credentials."""
    tail = url.rsplit("/", 1)[-1]
    return tail.split("?", 1)[0]


def _normalise(url: str) -> str:
    """Compare targets, ignoring driver and credentials."""
    body = url.split("://", 1)[-1]
    if "@" in body:
        body = body.rsplit("@", 1)[-1]
    return body.split("?", 1)[0].rstrip("/").lower()


def resolve_test_db_url() -> str | None:
    """The only sanctioned source of a database URL for tests.

    Returns None when no test database is configured, which skips the
    database tests. It never falls back to the application database — a
    convenient fallback is exactly how a test suite ends up truncating
    production.
    """
    url = (os.environ.get(TEST_DB_ENV) or "").strip()
    if not url:
        return None

    name = _dbname(url)
    if not name.endswith(REQUIRED_SUFFIX):
        pytest.exit(
            f"REFUSING TO RUN: {TEST_DB_ENV} points at database {name!r}, "
            f"which does not end in {REQUIRED_SUFFIX!r}. These tests truncate "
            f"tables; the target must be an obviously disposable database.",
            returncode=3)

    app_url = (os.environ.get(APP_DB_ENV) or "").strip()
    if app_url and _normalise(app_url) == _normalise(url):
        pytest.exit(
            f"REFUSING TO RUN: {TEST_DB_ENV} and {APP_DB_ENV} resolve to the "
            f"same database ({name!r}). Tests truncate tables and would "
            f"destroy application data.",
            returncode=3)
    return url


#: Resolved once, at import, so a misconfiguration stops the run immediately.
TEST_DB_URL = resolve_test_db_url()

#: The application database as configured BEFORE any test touched the
#: environment. tests/server/test_api.py rebinds VIRALLAB_DB_URL at runtime so
#: the app under test uses the disposable database, which would make a live
#: comparison meaningless.
APP_DB_URL_AT_IMPORT = (os.environ.get(APP_DB_ENV) or "").strip()


@pytest.fixture(scope="session")
def test_db_url() -> str:
    if not TEST_DB_URL:
        pytest.skip(f"{TEST_DB_ENV} not set")
    return TEST_DB_URL


def assert_disposable(url: str) -> None:
    """Belt-and-braces check callable from inside a test module."""
    assert _dbname(url).endswith(REQUIRED_SUFFIX), (
        f"refusing to truncate {_dbname(url)!r}: not a disposable test database")


@pytest.fixture(autouse=True)
def _no_host_spending_authorisation(monkeypatch, tmp_path_factory):
    """Tests must never read the machine's real spending authorisation.

    `config/spending.local.yaml` holds what a person actually approved on
    this deployment. The server has $5.00 and `apify` authorised, so six
    tests asserting "nothing is authorised by default" began failing the
    moment that file existed - not because the guard broke, but because the
    suite was reading production state.

    Pointing the override at an empty path restores the property those
    tests are actually about: with no configured authorisation, nothing is
    authorised. A test that wants authorisation sets it explicitly.
    """
    from server import config

    def clear_cache() -> None:
        # Several test modules replace `config.settings` with a plain
        # stub function, which has no `cache_clear`. Calling it
        # unconditionally turned a passing test into a teardown ERROR.
        clear = getattr(config.settings, "cache_clear", None)
        if callable(clear):
            clear()

    empty = tmp_path_factory.mktemp("no-spending") / "absent.yaml"
    monkeypatch.setattr(config, "SPENDING_OVERRIDE_PATH", empty,
                        raising=False)
    clear_cache()
    yield
    clear_cache()
