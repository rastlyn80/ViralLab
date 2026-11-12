"""Every third-party package the server imports must be declared.

## Why this test exists

On 12 November 2026 the Ubuntu machine was wiped and rebuilt. The restore
guide said `pip install -r requirements-server.txt`, which is what anyone
would do, and the result was a server whose labelling pages answered
"could not decode frames: No module named 'PIL'". Pillow was used in
three places in `server/` and had never been declared; it worked on the
old machine only because it had once been installed by hand.

What made it invisible was the *shape* of the import. All three uses are
inside functions:

    def encode_frames(...):
        from PIL import Image

A module-level import would have broken at startup on the first deploy
and been fixed the same day. A lazy one breaks on the first click of one
page, months later, on a machine nobody is watching. Importing every
module to check - which is what I tried first - finds nothing, because a
deferred import is not executed by importing its module.

So this test reads the source instead of running it, and treats an
indented import exactly like a top-level one.

## What a failure means

Either the package belongs in `requirements-server.txt`, or the import is
genuinely optional and belongs in `OPTIONAL` below with the guard that
makes it optional. "It works on my machine" is the condition this test
exists to catch, so it is never the right fix.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
REQUIREMENTS = ROOT / "requirements-server.txt"
PACKAGES = (ROOT / "server", ROOT / "shared")

#: Packages whose import name differs from the name pip installs.
#: Checked against the requirements file by distribution name.
DISTRIBUTION_NAME = {
    "PIL": "pillow",
    "yaml": "pyyaml",
    "cv2": "opencv-python",
    "psycopg": "psycopg",
    "dotenv": "python-dotenv",
    "jose": "python-jose",
    "yt_dlp": "yt-dlp",
    "dateutil": "python-dateutil",
    "sklearn": "scikit-learn",
}

#: Imports that are allowed to be absent because the code asks before
#: using them. Each entry names the module and the reason; a package
#: only belongs here if an ImportError around it is handled and the
#: feature degrades rather than crashing.
OPTIONAL = {
    # server/ai/providers.py: the local-OCR provider reports itself
    # unavailable when this is missing, which is the documented state on
    # a machine that has not installed it.
    "rapidocr_onnxruntime": "guarded by LocalOcr.available()",
}

#: Packages that belong to the GPU worker and are deliberately absent
#: from the server, even though `shared/` imports them. The server
#: imports those modules for their method constants, never to run the
#: model. Each must raise a deliberate error rather than a bare
#: ModuleNotFoundError if a server path ever reaches it - that is what
#: `test_worker_only_imports_fail_with_an_explanation` checks, and it is
#: the difference between a diagnosable failure and the Pillow outage.
WORKER_ONLY = {
    "faster_whisper": "ASR is a worker modality (CLAUDE.md §5); the "
                      "server reads shared/hookspeech.py constants only",
}

#: First-party packages, which are not pip dependencies.
FIRST_PARTY = {"server", "shared", "worker", "tests", "probes", "scripts"}


def declared() -> set[str]:
    """Distribution names in requirements-server.txt, lowercased."""
    names = set()
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        # "uvicorn[standard]>=1.0" -> "uvicorn"
        name = re.split(r"[\[<>=!;~ ]", line, maxsplit=1)[0]
        if name:
            names.add(name.strip().lower().replace("_", "-"))
    return names


def imported() -> dict[str, list[str]]:
    """Top-level package name -> where it is imported from.

    Walks the AST, so an import nested inside a function or a `try`
    counts the same as one at the top of the file. That is the whole
    point: the Pillow outage was a nested import.
    """
    found: dict[str, list[str]] = {}
    for package in PACKAGES:
        for path in sorted(package.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError as exc:  # pragma: no cover - would fail louder elsewhere
                pytest.fail(f"{path} does not parse: {exc}")
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    # `from . import x` and `from .foo import x` are local.
                    if node.level or not node.module:
                        continue
                    roots = [node.module.split(".")[0]]
                else:
                    continue
                for root in roots:
                    found.setdefault(root, []).append(
                        str(path.relative_to(ROOT)).replace("\\", "/"))
    return found


def test_requirements_file_parses() -> None:
    names = declared()
    assert "fastapi" in names and "sqlalchemy" in names, (
        "requirements-server.txt did not parse as expected; the rest of "
        "this module's conclusions would be meaningless")


def test_every_imported_package_is_declared_or_optional() -> None:
    names = declared()
    undeclared: dict[str, list[str]] = {}

    for root, places in sorted(imported().items()):
        if root in FIRST_PARTY or root in OPTIONAL or root in WORKER_ONLY:
            continue
        if root in sys.stdlib_module_names:
            continue
        dist = DISTRIBUTION_NAME.get(root, root).lower().replace("_", "-")
        if dist in names:
            continue
        undeclared[root] = sorted(set(places))

    if undeclared:
        lines = []
        for root, places in sorted(undeclared.items()):
            dist = DISTRIBUTION_NAME.get(root, root)
            shown = ", ".join(places[:4])
            more = f" (+{len(places) - 4} more)" if len(places) > 4 else ""
            lines.append(f"  {root!r} (pip: {dist}) imported by {shown}{more}")
        pytest.fail(
            "these packages are imported by the server but not declared in "
            "requirements-server.txt, so a rebuilt environment will not have "
            "them:\n" + "\n".join(lines) + "\n\n"
            "Add each to requirements-server.txt, or - only if an ImportError "
            "around it is handled and the feature degrades - add it to "
            "OPTIONAL in this file with the guard that makes it optional.")


def test_pillow_specifically_stays_declared() -> None:
    """The one that actually broke, named so a regression is unambiguous."""
    assert "pillow" in declared(), (
        "Pillow is imported inside server/api/frames_api.py and "
        "server/ai/providers.py. Without it the labelling pages fail with "
        "\"No module named 'PIL'\" - which is exactly what happened after "
        "the 12 November 2026 rebuild.")


def test_optional_imports_really_are_guarded() -> None:
    """An entry in OPTIONAL must have an ImportError handler near it.

    Otherwise OPTIONAL becomes a place to silence this test rather than a
    record of a genuinely optional feature.
    """
    sources = {
        path: path.read_text(encoding="utf-8")
        for package in PACKAGES
        for path in package.rglob("*.py")
        if "__pycache__" not in path.parts
    }
    for module in OPTIONAL:
        users = [p for p, text in sources.items() if module in text]
        assert users, (
            f"{module!r} is listed in OPTIONAL but nothing imports it; "
            f"remove the entry")
        guarded = [
            p for p in users
            if re.search(r"except\s+\(?[^)\n]*(ImportError|ModuleNotFoundError)",
                         sources[p])
        ]
        assert guarded, (
            f"{module!r} is listed in OPTIONAL as "
            f"{OPTIONAL[module]!r}, but no file importing it handles "
            f"ImportError. Either guard it or declare it as a dependency.")


def test_worker_only_imports_fail_with_an_explanation() -> None:
    """A worker-only import must not reach the server as a bare crash.

    The server is allowed not to have these installed. What it is not
    allowed to do is fail the way Pillow failed - with a message that
    names a missing module and says nothing about why it is missing or
    whether the caller was wrong. So each one is required to sit behind
    a handler that raises something explanatory.
    """
    sources = {
        path: path.read_text(encoding="utf-8")
        for package in PACKAGES
        for path in package.rglob("*.py")
        if "__pycache__" not in path.parts
    }
    for module, reason in WORKER_ONLY.items():
        users = [p for p, text in sources.items() if module in text]
        assert users, (
            f"{module!r} is listed in WORKER_ONLY but nothing imports it; "
            f"remove the entry")
        for path in users:
            text = sources[path]
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Import, ast.ImportFrom)):
                    continue
                names = ([a.name for a in node.names]
                         if isinstance(node, ast.Import)
                         else [node.module or ""])
                if not any((n or "").split(".")[0] == module for n in names):
                    continue
                handled = any(
                    isinstance(outer, ast.Try)
                    and node in list(ast.walk(outer))
                    and any("ImportError" in ast.dump(h)
                            or "ModuleNotFoundError" in ast.dump(h)
                            for h in outer.handlers)
                    for outer in ast.walk(tree)
                    if isinstance(outer, ast.Try))
                rel = str(path.relative_to(ROOT)).replace("\\", "/")
                assert handled, (
                    f"{rel}:{node.lineno} imports {module!r} without "
                    f"handling ModuleNotFoundError. It is listed as "
                    f"worker-only ({reason}), so the server will not have "
                    f"it - wrap the import and raise an error that says so, "
                    f"rather than letting it surface as "
                    f"\"No module named '{module}'\".")
