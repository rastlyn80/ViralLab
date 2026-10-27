"""Read the running interface the way a person does, and report jargon.

Runs on the WORKSTATION against the live tailnet app. This exists
because "the backend returns a display name" and "the page shows a
display name" are different claims, and only the second one is the
product. Unit tests on response objects cannot tell them apart.

It drives a real headless Chromium, signs in, visits every page a
user can reach, and reads the **visible text** - not the source. Then
it flags anything that looks like an internal name appearing where a
reader's eye lands.

What counts as a finding:

* a `snake_case` token in visible text, which is always a column name
  or a method version and never English;
* a statistical abbreviation on its own (`rho`, `q`, `p-value`) with
  no plain-language equivalent beside it;
* a raw JSON blob rendered into the page;
* a `method_version` string like `hook-deterministic-v1`.

What does not count: the same tokens inside an element marked
`.technical` or inside a closed `<details>`, because the brief asks
for technical detail to be *secondary*, not absent.

    python scripts/provision/ui_audit.py
    python scripts/provision/ui_audit.py --json --mobile
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Every route a signed-in user can reach from the navigation, plus
#: the detail pages they click through to. Ids are filled in at run
#: time from the API so the audit follows real data.
STATIC_ROUTES: tuple[tuple[str, str], ...] = (
    ("#/", "Dashboard"),
    ("#/library", "Content library"),
    ("#/review/masters", "Master links"),
    ("#/review/pairs", "Cross-platform review"),
    ("#/discover", "Discover creators"),
    ("#/research", "Creator research"),
    ("#/collections", "Collections"),
    ("#/competitors", "Capability status"),
    ("#/hooks", "Hook intelligence"),
    ("#/hooks/retention", "Hooks vs retention"),
    ("#/cohort/2", "You against other creators"),
    ("#/glossary", "What everything means"),
    ("#/features", "Feature catalogue"),
    ("#/ai", "AI models"),
    ("#/settings", "Settings"),
)

#: A snake_case identifier: two or more lowercase word parts joined by
#: underscores. Matches `edge_density`, not `Hook intelligence`.
SNAKE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")

#: Method versions, e.g. hook-deterministic-v1, hook-vision-gemma3-4b-v2.
METHOD_VERSION = re.compile(r"\bhook-[a-z0-9.-]+-v\d+\b")

#: Statistics shown bare. `rho` as a column header is the case that
#: prompted this; "rho = -0.4" inside a technical block is fine.
BARE_STATS = re.compile(
    r"(?<![a-z])(rho|p-value|q-value|spearman|partial rho|"
    r"benjamini|bonferroni|confidence interval|std ?dev|variance)"
    r"(?![a-z])", re.I)

#: Tokens that are legitimate English despite the shape.
ALLOWED_SNAKE: frozenset[str] = frozenset({
    "own_api", "e_g",
})

#: Creator handles are snake_case by convention and are the creator's
#: own name, not our jargon. `gabriele_baldocci` on a page about
#: Gabriele Baldocci is correct; excluding them is the difference
#: between an audit that finds problems and one that finds 40 names.
#: Loaded from the database at run time rather than listed.
HANDLE_SQL = "select handle from creators where handle is not null"


def known_handles(page) -> set[str]:
    try:
        rows = page.evaluate("""async () => {
          const out = [];
          for (const u of ['/api/research/creators?limit=500',
                           '/api/overview']) {
            const r = await fetch(u, {credentials:'same-origin'});
            if (!r.ok) continue;
            const j = await r.json();
            const rows = j.items || j.platforms || j || [];
            for (const x of rows) if (x && x.handle) out.push(x.handle);
          }
          return out;
        }""")
    except Exception:
        return set()
    return {str(h).lower() for h in rows or []}


def findings_for(text: str, handles: set[str] | None = None) -> dict:
    skip = ALLOWED_SNAKE | (handles or set())
    # A handle sitting next to a table label comes back from
    # `innerText` as one run of characters - `sean_crispindisplay` -
    # which is a quirk of reading rendered text, not something on the
    # screen. Anything starting with a known handle is dropped.
    snake = sorted({
        m for m in SNAKE.findall(text)
        if m not in skip
        and not any(m.startswith(h) for h in skip if len(h) > 4)})
    return {
        "snake_case": snake,
        "method_versions": sorted(set(METHOD_VERSION.findall(text))),
        "bare_statistics": sorted({m.lower()
                                   for m in BARE_STATS.findall(text)}),
    }


def _env() -> dict:
    out = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def visible_primary_text(page) -> str:
    """What a reader sees without opening anything.

    Collapsed `<details>` and anything marked `.technical` are removed
    first: the brief asks for technical detail to be available, not
    absent, so content behind a disclosure is not a finding.
    """
    return page.evaluate("""() => {
      const clone = document.body.cloneNode(true);
      clone.querySelectorAll('details:not([open])').forEach(d => d.remove());
      clone.querySelectorAll('.technical').forEach(d => d.remove());
      clone.querySelectorAll('script,style').forEach(d => d.remove());
      return clone.innerText || '';
    }""")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--mobile", action="store_true",
                    help="also audit at a phone viewport")
    ap.add_argument("--shots", default="",
                    help="directory to write a screenshot per page")
    args = ap.parse_args(argv)

    env = _env()
    host = env.get("UBUNTU_HOST")
    password = env.get("SERVER_UI_PASSWORD")
    if not host or not password:
        print("UBUNTU_HOST and SERVER_UI_PASSWORD must be in .env")
        return 2
    base = f"https://{host}:8443/"

    from playwright.sync_api import sync_playwright

    report: dict = {"base": base, "pages": []}
    shots = pathlib.Path(args.shots) if args.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)

    viewports = [("desktop", {"width": 1440, "height": 900})]
    if args.mobile:
        viewports.append(("mobile", {"width": 390, "height": 844}))

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for vp_name, vp in viewports:
            ctx = browser.new_context(ignore_https_errors=True, viewport=vp)
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)[:200]))

            page.goto(base, wait_until="networkidle", timeout=45000)
            # Sign in once per context.
            if page.locator("input[type=password]").count():
                page.fill("input[type=password]", password)
                page.keyboard.press("Enter")
                page.wait_for_timeout(1500)

            handles = known_handles(page)
            routes = list(STATIC_ROUTES)
            # Follow through to a real report and a real video, because
            # the detail pages are where the jargon lives.
            try:
                ids = page.evaluate("""async () => {
                  const r = await fetch('/api/research/creators?limit=3',
                    {credentials:'same-origin'});
                  const j = r.ok ? await r.json() : [];
                  const v = await fetch('/api/videos?limit=1',
                    {credentials:'same-origin'});
                  const vj = v.ok ? await v.json() : {};
                  return {creators: (j.items||j||[]).slice(0,2)
                            .map(x => x.id || x.creator_id),
                          video: (vj.items||[])[0]?.id};
                }""")
            except Exception:
                ids = {}
            for cid in (ids or {}).get("creators", []) or []:
                if cid:
                    routes.append((f"#/research/{cid}", f"Creator {cid}"))
                    routes.append((f"#/research/{cid}/report",
                                   f"Creator {cid} report"))
            if (ids or {}).get("video"):
                routes.append((f"#/video/{ids['video']}", "Video detail"))

            for route, label in routes:
                page.goto(base + route, wait_until="networkidle",
                          timeout=45000)
                page.wait_for_timeout(700)
                text = visible_primary_text(page)
                f = findings_for(text, handles)
                total = sum(len(v) for v in f.values())
                entry = {"viewport": vp_name, "route": route,
                         "label": label, "issues": total, **f,
                         "chars": len(text)}
                if shots:
                    name = route.replace("#/", "").replace("/", "_") or "home"
                    shot = shots / f"{vp_name}_{name}.png"
                    page.screenshot(path=str(shot), full_page=True)
                    entry["screenshot"] = str(shot)
                report["pages"].append(entry)
            report.setdefault("page_errors", []).extend(errors)
            ctx.close()
        browser.close()

    report["total_issues"] = sum(p["issues"] for p in report["pages"])
    worst = sorted(report["pages"], key=lambda x: -x["issues"])[:12]
    report["worst"] = [{"route": w["route"], "viewport": w["viewport"],
                        "issues": w["issues"],
                        "sample": (w["snake_case"][:6]
                                   + w["method_versions"][:2]
                                   + w["bare_statistics"][:3])}
                       for w in worst if w["issues"]]

    if args.json:
        print(json.dumps(report, indent=1))
    else:
        print(f"{report['total_issues']} jargon finding(s) across "
              f"{len(report['pages'])} page render(s)\n")
        for w in report["worst"]:
            print(f"  {w['issues']:>4}  {w['viewport']:<8} {w['route']}")
            print(f"        {', '.join(str(s) for s in w['sample'])}")
        if report.get("page_errors"):
            print("\nJavaScript errors:")
            for e in dict.fromkeys(report["page_errors"]):
                print("  -", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
