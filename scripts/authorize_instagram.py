"""Complete Instagram authorization on the Facebook Login path (ADR 0001/0005).

The owner performs the consent in a browser and pastes back a short-lived
User token. This script then does the rest automatically:

    short-lived User token
      -> long-lived User token        (60 days)
      -> Page token + IG Business id  (Page tokens do NOT expire)
      -> stored on the AUTHORITATIVE server, never locally

Token values are never printed, never logged, and never passed on a command
line — the token is read from stdin and travels to the server inside the
process.

    python scripts/authorize_instagram.py --token-stdin
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from remote import connect, env, run  # noqa: E402

REMOTE = "/home/rastlyn/virallab"
GRAPH = "https://graph.facebook.com"
VERSION = "v22.0"

#: Facebook Login scope family (ADR 0001). The short forms were deprecated
#: in January 2025; these full names are required.
REQUIRED_SCOPES = ["instagram_basic", "instagram_manage_insights",
                   "pages_show_list", "pages_read_engagement"]

#: Wanted, but not required — the project works without it and has done so
#: far. `ads_read` is what turns the paid/organic separation from an upper
#: bound into the real figure (ADR 0008): with it, Ads Insights reports
#: spend, impressions, reach and the paid watch-time distribution per
#: boosted post. Reported rather than demanded, so an authorisation that
#: omits it still succeeds instead of locking the owner out of collection.
#:
#: Note it is read from a USER token, not the Page token this script
#: stores, so granting it adds a credential rather than replacing one.
OPTIONAL_SCOPES = {
    "ads_read": ("paid/organic separation via Ads Insights — without it "
                 "the ad-exposure covariate stays an upper bound "
                 "(docs/adr/0008-paid-organic-separation.md §4)"),
}


def fail(msg: str) -> int:
    print(f"\n  FAILED: {msg}", file=sys.stderr)
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token-stdin", action="store_true",
                    help="read the short-lived token from stdin (preferred)")
    ap.add_argument("--ig-username",
                    help="pick this Instagram account when several are linked; "
                         "avoids an interactive prompt when the token arrived "
                         "on stdin")
    args = ap.parse_args()

    e = env()
    app_id, app_secret = e.get("META_APP_ID"), e.get("META_APP_SECRET")
    if not app_id or not app_secret:
        return fail("META_APP_ID / META_APP_SECRET missing from .env")

    if args.token_stdin:
        short = sys.stdin.read().strip()
    else:
        short = getpass.getpass("Paste the short-lived User token (hidden): ")
    short = short.strip()
    if not short:
        return fail("no token supplied")
    print(f"  received a token of {len(short)} characters (value not shown)")

    http = httpx.Client(timeout=45.0)

    # 1. short-lived -> long-lived User token (60 days)
    r = http.get(f"{GRAPH}/{VERSION}/oauth/access_token", params={
        "grant_type": "fb_exchange_token", "client_id": app_id,
        "client_secret": app_secret, "fb_exchange_token": short})
    data = r.json()
    if "access_token" not in data:
        return fail(f"exchange failed: "
                    f"{data.get('error', {}).get('message', r.status_code)}")
    long_user = data["access_token"]
    print(f"  long-lived User token obtained (expires_in="
          f"{data.get('expires_in', 'n/a')}s)")

    # 2. confirm the granted scopes before going further
    dbg = http.get(f"{GRAPH}/{VERSION}/debug_token", params={
        "input_token": long_user,
        "access_token": f"{app_id}|{app_secret}"}).json().get("data", {})
    granted = set(dbg.get("scopes") or [])
    missing = [s for s in REQUIRED_SCOPES if s not in granted]
    print(f"  scopes granted: {len(granted)}")
    if missing:
        return fail("missing required scope(s): " + ", ".join(missing)
                    + "\n  Re-run the consent step and tick all four.")
    print("  all required scopes present")
    for scope, why in OPTIONAL_SCOPES.items():
        if scope in granted:
            print(f"  OPTIONAL scope {scope} IS granted: {why}")
        else:
            print(f"  optional scope {scope} not granted - {why}")

    # 3. Page token + linked Instagram Business account
    pages = http.get(f"{GRAPH}/{VERSION}/me/accounts", params={
        "access_token": long_user,
        "fields": "id,name,access_token,instagram_business_account{id,username}",
    }).json().get("data", [])
    if not pages:
        return fail("no Facebook Pages found. The Instagram account must be "
                    "Professional and linked to a Page you administer.")

    linked = [p for p in pages if p.get("instagram_business_account")]
    print(f"  Pages: {len(pages)}, with a linked Instagram account: {len(linked)}")
    if not linked:
        return fail("no Page has a linked Instagram Business account. "
                    "Link it in Instagram > Settings > Linked accounts.")
    for i, p in enumerate(linked):
        ig = p["instagram_business_account"]
        print(f"    [{i}] Page {p['name']!r} -> @{ig.get('username')}")
    if len(linked) > 1:
        if args.ig_username:
            wanted = args.ig_username.lstrip("@").lower()
            match = [i for i, p in enumerate(linked)
                     if (p["instagram_business_account"].get("username") or "")
                     .lower() == wanted]
            if not match:
                return fail(f"no linked account matches @{wanted}")
            idx = match[0]
        elif sys.stdin.isatty():
            idx = int(input("  choose index: ").strip())
        else:
            return fail("several linked accounts; re-run with --ig-username")
    else:
        idx = 0

    page = linked[idx]
    ig = page["instagram_business_account"]
    ig_id, ig_user = ig["id"], ig.get("username", "")
    print(f"  selected: Page {page['name']!r} -> @{ig_user}")

    # 4. store on the AUTHORITATIVE server. Page tokens do not expire, so
    #    expires_in is deliberately absent (ADR 0005).
    payload = {"platform": "instagram", "account_ref": ig_id,
               "access_token": page["access_token"], "page_id": page["id"],
               "scope": ",".join(sorted(granted)), "username": ig_user}

    c = connect()
    try:
        tmp = Path(__file__).resolve().parent.parent / ".ig_token.json"
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        sftp = c.open_sftp()
        sftp.put(str(tmp), f"{REMOTE}/.ig_token.json")
        sftp.chmod(f"{REMOTE}/.ig_token.json", 0o600)
        sftp.close()
        tmp.unlink()
        rc, out, err = run(
            c, f"cd {REMOTE} && set -a && . ./.env && set +a && "
               f".venv/bin/python store_ig_token.py", timeout=300)
        print(out.strip() or err.strip())
        run(c, f"rm -f {REMOTE}/.ig_token.json", timeout=60)
        return rc
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
