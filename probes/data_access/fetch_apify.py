"""Phase 0A sample fetcher (Apify) — stdlib only, no dependencies.

Runs one Apify actor, waits for it, saves the raw dataset to JSON, and
reports the run's actual billed cost so section 7 of the 0A report quotes a
measured figure rather than a pricing page.

    export APIFY_TOKEN=apify_api_...
    python fetch_apify.py --actor clockworks/tiktok-scraper \
        --input inputs/tiktok_hashtag.json --max-items 200 --out sample_tiktok.json

Always dry-run first; it costs nothing and prints exactly what would happen:

    python fetch_apify.py --actor ... --input ... --dry-run

SPEND GUARD. The free plan carries $5 of credit that cannot be topped up and
expires monthly, so an accidental large run is unrecoverable for the month.
Three protections, all on by default:
  * --max-items is required and capped at MAX_ITEMS_HARD_CAP
  * the actor input is rewritten to carry the limit under several common key
    names, so a limit is enforced actor-side as well as locally
  * --max-usd aborts the run if its reported cost passes the ceiling

Raw output is never reshaped — score_provider.py adapts via aliases instead,
so the saved file stays byte-for-byte what the provider returned.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://api.apify.com/v2"
MAX_ITEMS_HARD_CAP = 1000      # refuse anything larger regardless of flags
DEFAULT_MAX_USD = 1.50         # abort a run that costs more than this
POLL_SECONDS = 10
POLL_TIMEOUT_SECONDS = 1800

# Actor input keys used for "how many results" vary by actor; set all we know.
LIMIT_KEYS = ("resultsLimit", "maxItems", "limit", "maxResults",
              "resultsPerPage", "maxPostCount")


def call(method: str, path: str, token: str, payload: dict | None = None):
    url = "{}/{}?token={}".format(API, path.lstrip("/"), token)
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise SystemExit("HTTP {} on {} {}\n{}".format(
            exc.code, method, path, detail))
    except urllib.error.URLError as exc:
        raise SystemExit("network error on {} {}: {}".format(method, path, exc))
    return json.loads(body) if body.strip() else {}


def run_cost(run: dict) -> float | None:
    for key in ("usageTotalUsd", "costUsd"):
        if isinstance(run.get(key), (int, float)):
            return float(run[key])
    usage = run.get("usageUsd")
    if isinstance(usage, dict):
        return float(sum(v for v in usage.values() if isinstance(v, (int, float))))
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 0A Apify sample fetcher")
    ap.add_argument("--actor", required=True,
                    help="actor id, e.g. clockworks/tiktok-scraper")
    ap.add_argument("--input", required=True, type=Path,
                    help="JSON file with the actor's input")
    ap.add_argument("--out", type=Path, help="where to save raw items")
    ap.add_argument("--max-items", required=True, type=int,
                    help="hard result cap (<= {})".format(MAX_ITEMS_HARD_CAP))
    ap.add_argument("--max-usd", type=float, default=DEFAULT_MAX_USD)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and exit without calling Apify")
    args = ap.parse_args()

    if args.max_items < 1 or args.max_items > MAX_ITEMS_HARD_CAP:
        raise SystemExit("--max-items must be 1..{} (spend guard)".format(
            MAX_ITEMS_HARD_CAP))

    try:
        actor_input = json.loads(args.input.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit("cannot read --input {}: {}".format(args.input, exc))

    # Enforce the cap actor-side too, under every key name we know of.
    found_limit = False
    for key in LIMIT_KEYS:
        if key in actor_input:
            actor_input[key] = min(int(actor_input[key] or args.max_items),
                                   args.max_items)
            found_limit = True
    # Only invent a limit key if the input declares none: actors validate
    # against their own input schema and may reject unknown properties.
    if not found_limit:
        actor_input["resultsLimit"] = args.max_items

    actor_path = args.actor.replace("/", "~")
    out = args.out or Path("sample_{}.json".format(
        args.actor.split("/")[-1].replace("-", "_")))

    print("\nPhase 0A fetch plan")
    print("  actor      : {}".format(args.actor))
    print("  input file : {}".format(args.input))
    print("  max items  : {} (hard cap {})".format(args.max_items,
                                                   MAX_ITEMS_HARD_CAP))
    print("  cost ceiling: ${:.2f}".format(args.max_usd))
    print("  output     : {}".format(out))
    print("  input sent :\n{}".format(json.dumps(actor_input, indent=4)))

    if args.dry_run:
        print("\n  DRY RUN - nothing called, nothing spent.\n")
        return 0

    token = os.environ.get("APIFY_TOKEN", "").strip()
    if not token:
        raise SystemExit("APIFY_TOKEN is not set; refusing to continue")

    print("\n  starting run...")
    run = call("POST", "acts/{}/runs".format(actor_path), token,
               actor_input).get("data", {})
    run_id, dataset_id = run.get("id"), run.get("defaultDatasetId")
    if not run_id:
        raise SystemExit("no run id returned: {}".format(run))
    print("  run id: {}  dataset: {}".format(run_id, dataset_id))

    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    status, cost = run.get("status"), None
    while status in (None, "READY", "RUNNING"):
        if time.monotonic() > deadline:
            print("  TIMEOUT - aborting run {}".format(run_id))
            call("POST", "actor-runs/{}/abort".format(run_id), token, {})
            raise SystemExit("run exceeded {}s".format(POLL_TIMEOUT_SECONDS))
        time.sleep(POLL_SECONDS)
        run = call("GET", "actor-runs/{}".format(run_id), token).get("data", {})
        status, cost = run.get("status"), run_cost(run)
        print("  status={} cost={}".format(
            status, "${:.4f}".format(cost) if cost is not None else "n/a"))
        if cost is not None and cost > args.max_usd:
            print("  COST CEILING EXCEEDED - aborting")
            call("POST", "actor-runs/{}/abort".format(run_id), token, {})
            raise SystemExit("run cost ${:.4f} > ceiling ${:.2f}".format(
                cost, args.max_usd))

    print("  final status: {}".format(status))
    items = call("GET", "datasets/{}/items".format(
        run.get("defaultDatasetId") or dataset_id), token)
    if not isinstance(items, list):
        items = [items]
    out.write_text(json.dumps(items, indent=1), encoding="utf-8")

    n = len(items)
    per_1k = (cost / n * 1000) if cost and n else None
    print("\n  saved {} records -> {}".format(n, out))
    print("  billed: {}".format(
        "${:.4f}".format(cost) if cost is not None else "not reported"))
    if per_1k is not None:
        print("  MEASURED cost per 1,000 results: ${:.2f}".format(per_1k))

    meta = out.with_suffix(".run.json")
    meta.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "actor": args.actor, "run_id": run_id, "status": status,
        "records": n, "billed_usd": cost, "measured_usd_per_1k": per_1k,
        "actor_input": actor_input,
    }, indent=2), encoding="utf-8")
    print("  provenance -> {}".format(meta))
    print("\n  next: python score_provider.py {} --provider \"{}\"\n".format(
        out, args.actor))
    return 0 if status == "SUCCEEDED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
