"""Generate `webui/types.js` JSDoc typedefs from the API's OpenAPI schema.

This is how the frontend is typed without a compiler (ADR 0006). The
Pydantic models in `shared/schemas/ui.py` are the single source of truth;
this turns them into typedefs an editor can check call sites against, so the
contract is never transcribed by hand into a second place where it can drift.

    python scripts/gen_ui_types.py          # write webui/types.js
    python scripts/gen_ui_types.py --check  # fail if it is out of date

`--check` runs in the test suite, so a model change that nobody regenerated
is a test failure rather than a silently stale type.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "webui" / "types.js"

HEADER = """/**
 * GENERATED FILE - do not edit.
 *
 * Produced by scripts/gen_ui_types.py from the FastAPI OpenAPI schema, which
 * is itself generated from the Pydantic models in shared/schemas/ui.py.
 * Those models are the single source of truth for the API boundary
 * (ADR 0006); this file exists so an editor can check the frontend against
 * them without a build step.
 *
 * Regenerate with:  python scripts/gen_ui_types.py
 */

"""

PRIMITIVES = {
    "string": "string",
    "integer": "number",
    "number": "number",
    "boolean": "boolean",
    "object": "Object",
    "null": "null",
}


def js_type(schema: dict, name_hint: str = "") -> str:
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    if "anyOf" in schema or "oneOf" in schema:
        parts = [js_type(s) for s in schema.get("anyOf") or schema["oneOf"]]
        # `string|null` is how Pydantic renders Optional; keep it explicit so
        # the frontend is forced to handle the null case.
        seen, uniq = set(), []
        for p in parts:
            if p not in seen:
                seen.add(p)
                uniq.append(p)
        return "|".join(uniq)
    t = schema.get("type")
    if t == "array":
        inner = js_type(schema.get("items") or {}, name_hint)
        return f"Array<{inner}>"
    if t == "object" or t is None:
        extra = schema.get("additionalProperties")
        if isinstance(extra, dict):
            return f"Object<string, {js_type(extra)}>"
        return "Object"
    return PRIMITIVES.get(t, "*")


def render(spec: dict) -> str:
    schemas = (spec.get("components") or {}).get("schemas") or {}
    out = [HEADER]
    for name in sorted(schemas):
        s = schemas[name]
        if s.get("enum"):
            vals = "|".join(f'"{v}"' for v in s["enum"])
            out.append(f"/** @typedef {{{vals}}} {name} */\n\n")
            continue
        props = s.get("properties") or {}
        if not props:
            continue
        required = set(s.get("required") or [])
        lines = [f"/**\n * @typedef {{Object}} {name}"]
        if s.get("description"):
            first = s["description"].strip().splitlines()[0]
            lines.insert(1, f" * {first}")
        for prop in props:
            ps = props[prop]
            jt = js_type(ps, prop)
            opt = "" if prop in required else "="
            desc = (ps.get("description") or "").strip().replace("\n", " ")
            suffix = f" {desc}" if desc else ""
            lines.append(f" * @property {{{jt}{opt}}} {prop}{suffix}")
        lines.append(" */\n\n")
        out.append("\n".join(lines))
    out.append("export {};\n")
    return "".join(out)


def build_spec() -> dict:
    # The app needs these to import; values are irrelevant for schema
    # generation and nothing connects to a database here.
    os.environ.setdefault("VIRALLAB_DB_URL",
                          "postgresql+psycopg://unused:unused@127.0.0.1/unused")
    os.environ.setdefault("VIRALLAB_MEDIA_SECRET", "x" * 32)
    from server.api.app import app
    return app.openapi()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if webui/types.js is out of date")
    args = ap.parse_args()

    text = render(build_spec())
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print("webui/types.js is out of date; run "
                  "python scripts/gen_ui_types.py")
            return 1
        print(f"webui/types.js is current ({len(text.splitlines())} lines)")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    n = text.count("@typedef")
    print(f"  wrote {OUT.relative_to(ROOT)} - {n} typedef(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
