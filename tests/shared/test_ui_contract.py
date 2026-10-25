"""The frontend's types must match the backend's models.

`webui/types.js` is generated from the OpenAPI schema, which is generated
from the Pydantic models. This test makes that a guarantee rather than a
convention: change a model without regenerating and the suite fails, instead
of the frontend quietly drifting from the contract (ADR 0006).

The comparison is **semantic, not textual**. An earlier version shelled out
to `scripts/gen_ui_types.py --check` and compared byte-for-byte, which made
the test fail on the server for two unrelated reasons: `scripts/` is not
deployed there, and a different FastAPI version formats the schema slightly
differently. Comparing the set of type names and property names is portable
and still catches real drift.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
TYPES = ROOT / "webui" / "types.js"

#: Schemas the frontend never touches. The worker API shares the same app, so
#: its job contracts appear in the same OpenAPI document.
IGNORED_PREFIXES = ("HTTPValidationError", "ValidationError")


def live_schemas() -> dict[str, set[str]]:
    os.environ.setdefault(
        "VIRALLAB_DB_URL",
        "postgresql+psycopg://unused:unused@127.0.0.1/unused")
    os.environ.setdefault("VIRALLAB_MEDIA_SECRET", "x" * 32)
    from server.api.app import app
    spec = app.openapi()
    out = {}
    for name, schema in (spec.get("components") or {}).get(
            "schemas", {}).items():
        if name.startswith(IGNORED_PREFIXES):
            continue
        props = set((schema.get("properties") or {}).keys())
        if props or schema.get("enum"):
            out[name] = props
    return out


def declared_typedefs() -> dict[str, set[str]]:
    text = TYPES.read_text(encoding="utf-8")
    out: dict[str, set[str]] = {}
    for block in text.split("/**")[1:]:
        m = re.search(r"@typedef\s+\{[^}]*\}\s+(\w+)", block)
        if not m:
            continue
        name = m.group(1)
        if name.startswith(IGNORED_PREFIXES):
            continue
        out[name] = set(
            re.findall(r"@property\s+\{[^}]*\}\s+(\w+)", block))
    return out


def test_generated_types_cover_every_api_model():
    live, declared = live_schemas(), declared_typedefs()
    missing = sorted(set(live) - set(declared))
    assert not missing, (
        f"these API models have no typedef: {missing}\n"
        f"Run: python scripts/gen_ui_types.py")


def test_no_typedef_describes_a_model_that_no_longer_exists():
    live, declared = live_schemas(), declared_typedefs()
    stale = sorted(set(declared) - set(live))
    assert not stale, (
        f"these typedefs describe models that are gone: {stale}\n"
        f"Run: python scripts/gen_ui_types.py")


def test_every_property_is_declared():
    live, declared = live_schemas(), declared_typedefs()
    drifted = {}
    for name, props in live.items():
        if name not in declared:
            continue
        gap = sorted(props - declared[name])
        if gap:
            drifted[name] = gap
    assert not drifted, (
        f"properties missing from webui/types.js: {drifted}\n"
        f"Run: python scripts/gen_ui_types.py")


def test_the_honesty_fields_are_part_of_the_contract():
    """Pins the fields the interface depends on to avoid lying.

    If any of these disappears from the API, the UI silently loses its
    ability to distinguish unknown from zero, a proposal from a fact, or a
    metric from a comparable metric.
    """
    live = live_schemas()
    assert "age_days" in live["MetricReading"]
    assert "saves" in live["MetricReading"]
    assert "skip_rate_pct" in live["MetricReading"]
    assert {"latest_views", "latest_age_days"} <= live["VideoListItem"]
    assert {"status", "decided_by"} <= live["MasterLinkView"]
    assert {"status", "decided_by"} <= live["PublicationLinkView"]
    assert {"implemented", "blocked_by", "phase"} <= live["CapabilityState"]
    assert "media_obtainable" in live["MediaCoverage"]
    assert "anchors" in live["VideoDetail"]


def test_every_frontend_module_is_an_es_module():
    """The SPA has no build step, so a missing export would only surface in
    a browser."""
    webui = ROOT / "webui"
    for name in ("app.js", "api.js", "components.js", "views.js"):
        text = (webui / name).read_text(encoding="utf-8")
        assert len(text) > 200, f"{name} looks empty"
        assert "export" in text or "import" in text, (
            f"{name} is not an ES module")
    index = (webui / "index.html").read_text(encoding="utf-8")
    assert index.count('type="module"') == 1


def test_the_frontend_only_calls_endpoints_that_exist():
    """Catches a renamed route that would 404 at runtime."""
    os.environ.setdefault(
        "VIRALLAB_DB_URL",
        "postgresql+psycopg://unused:unused@127.0.0.1/unused")
    os.environ.setdefault("VIRALLAB_MEDIA_SECRET", "x" * 32)
    from server.api.app import app
    paths = set(app.openapi().get("paths") or {})

    api_js = (ROOT / "webui" / "api.js").read_text(encoding="utf-8")
    called = set(re.findall(r"request\(\s*[`'\"](/api/[^`'\"${]*)", api_js))
    called |= {m.split("${")[0] for m in re.findall(
        r"request\(\s*`(/api/[^`]*)`", api_js)}

    for path in sorted(called):
        clean = path.rstrip("/")
        if not clean:
            continue
        # Templated paths appear in OpenAPI with a {param} placeholder.
        candidates = {p for p in paths
                      if p == clean or p.startswith(f"{clean}/")}
        assert candidates, f"api.js calls {clean!r}, which the API does not expose"
