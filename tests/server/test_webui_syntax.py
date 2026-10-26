"""The no-build front end has no build step, so nothing else catches this.

ADR 0006 chose plain ES modules served statically: no bundler, no
transpiler, no CI step that would otherwise parse the JavaScript. The
trade-off is that a syntax error ships silently and the page simply fails to
render.

This test exists because that happened. `webui/hooks.js` was written with a
single-quoted string spanning a newline inside a template expression - a
hard SyntaxError - and `node --check file.js` reported it as fine, because
without `--input-type=module` node parses a `.js` file as a CommonJS script
and takes a different path. The module-mode check catches it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

WEBUI = Path(__file__).resolve().parents[2] / "webui"
MODULES = sorted(WEBUI.glob("*.js"))

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is not available to parse the front end")


def test_there_are_modules_to_check():
    assert MODULES, "no webui modules found; the glob or layout changed"


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_module_parses_as_an_es_module(path: Path):
    """Parse each file the way the browser will.

    `--input-type=module` is the load-bearing flag. Checking without it
    passes files that the browser rejects.
    """
    out = subprocess.run(
        ["node", "--input-type=module", "--check"],
        input=path.read_bytes(), capture_output=True, timeout=60)
    assert out.returncode == 0, (
        f"{path.name} is not valid as an ES module:\n"
        f"{out.stderr.decode(errors='replace')[:800]}")


def test_every_named_import_resolves_to_an_export():
    """A missing export is a runtime failure a parser cannot see."""
    import re

    exports: dict[str, set[str]] = {}
    for path in MODULES:
        src = path.read_text(encoding="utf-8")
        names = set(re.findall(
            r"^export (?:const|function|class|let)\s+(\w+)", src, re.M))
        for block in re.findall(r"^export \{([^}]*)\}", src, re.M):
            names |= {x.strip().split()[-1]
                      for x in block.split(",") if x.strip()}
        exports[path.name] = names

    problems = []
    for path in MODULES:
        src = path.read_text(encoding="utf-8")
        for block, module in re.findall(
                r"import\s+\{([^}]*)\}\s+from\s+'\./([\w.]+)'", src, re.S):
            wanted = {x.strip() for x in block.replace("\n", " ").split(",")
                      if x.strip()}
            missing = wanted - exports.get(module, set())
            if missing:
                problems.append(
                    f"{path.name} imports {sorted(missing)} from {module}, "
                    f"which does not export them")
    assert not problems, "\n".join(problems)
