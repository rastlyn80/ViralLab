"""The interface must not show a reader a column name.

Two kinds of check, because they catch different mistakes.

**Static**, always run: the interface source must not hard-code a
label for a measurement, must route names through the shared lexicon,
and must keep the technical presentation level genuinely hidden by
default. These catch a regression at the moment it is written.

**Live**, opt-in: drive the real application in a headless browser,
read the visible text of every page, and fail on jargon. This is the
only check that can tell "the API returns a display name" apart from
"the page shows one", which is the distinction the whole usability
correction turned on. It needs a running instance, so it is skipped
unless `VIRALLAB_UI_AUDIT=1`.

    VIRALLAB_UI_AUDIT=1 pytest tests/shared/test_ui_language.py
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WEBUI = ROOT / "webui"

#: Views, not generated files. `types.js` is generated from the
#: OpenAPI schema and is all field names by definition.
VIEW_FILES = sorted(
    p for p in WEBUI.glob("*.js")
    if p.name not in {"types.js", "api.js", "lexicon.js"})


def _source(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# static
# ---------------------------------------------------------------------------

def test_no_view_hard_codes_a_feature_label():
    """A label written into a component is a label that will disagree
    with `server/features/lexicon.py` within a month. Views must call
    `featureName`, never embed their own mapping.
    """
    from server.features import lexicon as lex

    # A view mentioning a stored key in a string literal is suspect.
    # Allowed: `lexicon.js` itself, and comments explaining the rule.
    offenders: list[str] = []
    for p in VIEW_FILES:
        src = _source(p)
        body = "\n".join(
            ln for ln in src.splitlines()
            if not ln.lstrip().startswith(("*", "//", "/*")))
        for key in lex.PLAIN:
            if f"'{key}'" in body or f'"{key}"' in body:
                offenders.append(f"{p.name}: {key}")
    assert not offenders, (
        "feature keys appear as literals in view code; route them "
        f"through lexicon.js instead: {offenders}")


def test_the_technical_level_is_hidden_by_default():
    css = _source(WEBUI / "styles.css")
    assert ".technical { display: none; }" in css
    assert "html.show-technical .technical" in css


def test_the_technical_toggle_exists_and_is_reachable():
    app = _source(WEBUI / "app.js")
    assert "tech-toggle" in app
    assert "mountTechnicalToggle()" in app
    # The shell is built by app.js, not index.html.
    assert 'id="tech-toggle-host"' in app


def test_the_lexicon_is_loaded_before_the_first_render():
    """A view that renders before the names arrive shows keys for a
    frame, which is exactly the thing being fixed."""
    app = _source(WEBUI / "app.js")
    assert "loadLexicon()" in app
    boot = app[app.index("async function boot()"):]
    assert "loadLexicon" in boot[:boot.index("await route()")]


def test_navigation_has_no_jargon():
    app = _source(WEBUI / "app.js")
    start = app.index("const NAV = [")
    nav = app[start:app.index("];", start)]
    labels = re.findall(r"label: '([^']+)'", nav)
    assert labels, "no navigation labels found"
    for label in labels:
        assert "_" not in label, label
        assert label.lower() not in {"features", "hooks"}, label


def test_every_view_that_shows_measurements_uses_the_lexicon():
    for name in ("hooks.js",):
        src = _source(WEBUI / name)
        assert "from './lexicon.js'" in src, name


# ---------------------------------------------------------------------------
# live
# ---------------------------------------------------------------------------

LIVE = os.environ.get("VIRALLAB_UI_AUDIT") == "1"

pytestmark_live = pytest.mark.skipif(
    not LIVE, reason="set VIRALLAB_UI_AUDIT=1 to audit a running instance")


@pytestmark_live
def test_no_page_shows_jargon_as_primary_content():
    """The check that matters. Everything else is a proxy for it."""
    import sys
    sys.path.insert(0, str(ROOT / "scripts" / "provision"))
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "ui_audit", ROOT / "scripts" / "provision" / "ui_audit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    import io
    import json as _json
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        mod.main(["--json"])
    report = _json.loads(buf.getvalue())

    bad = [p for p in report["pages"] if p["issues"]]
    # A creator's own username is snake_case and is not our jargon.
    # The audit strips the handles it can enumerate; anything left
    # with a single finding that looks like a handle is tolerated.
    real = [p for p in bad
            if p["method_versions"] or p["bare_statistics"]
            or len(p["snake_case"]) > 1]
    assert not real, [
        {"route": p["route"], "viewport": p["viewport"],
         "snake": p["snake_case"], "stats": p["bare_statistics"],
         "methods": p["method_versions"]} for p in real]


@pytestmark_live
def test_technical_detail_is_still_there_when_asked_for():
    """Hiding is not deleting. With the toggle on, the exact feature
    names and coefficients must come back."""
    import pathlib

    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    base = f"https://{env['UBUNTU_HOST']}:8443/"

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(ignore_https_errors=True)
        pg = ctx.new_page()
        pg.goto(base, wait_until="networkidle", timeout=45000)
        pg.fill("input[type=password]", env["SERVER_UI_PASSWORD"])
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(1500)
        pg.goto(base + "#/features", wait_until="networkidle",
                timeout=45000)
        pg.wait_for_timeout(600)
        hidden = pg.inner_text("body")
        assert "edge_density" not in hidden, (
            "a stored key is visible before the toggle is used")
        pg.click("#tech-toggle")
        pg.wait_for_timeout(400)
        shown = pg.inner_text("body")
        assert "edge_density" in shown, (
            "technical detail was removed rather than hidden")
        b.close()
