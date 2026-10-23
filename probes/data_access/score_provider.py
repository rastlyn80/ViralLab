"""Phase 0A provider scorecard.

Scores a raw provider sample against probes/data_access/field_requirements.py
and prints a gate verdict. Standard library only, so it runs anywhere with no
install step.

    python score_provider.py sample.json --provider "apify/clockworks"

Input: a JSON array of records, a JSONL file, or a JSON object with a
"results"/"items"/"data" array. Records are read as-is; nested fields are
addressed with dotted aliases.

Exit code 0 = gate passed, 1 = gate failed, 2 = could not read input. The
nonzero exit is deliberate: this is the Phase 0A gate, and a failing provider
should be hard to overlook.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from field_requirements import (  # noqa: E402
    REQUIREMENTS, BLOCKING, IMPORTANT, NICE, CAPABILITY,
)

COMPLETENESS_THRESHOLD = 0.80  # plan section 7, Phase 0A gate condition 1


def load_records(path: Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    if raw[0] == "[":
        data = json.loads(raw)
    elif raw[0] == "{":
        obj = json.loads(raw)
        data = [obj]
        for key in ("results", "items", "data", "videos", "posts"):
            if isinstance(obj.get(key), list):
                data = obj[key]
                break
    else:  # JSONL
        data = [json.loads(line) for line in raw.splitlines() if line.strip()]
    return [r for r in data if isinstance(r, dict)]


def dig(record: dict, dotted: str):
    cur = record
    for part in dotted.split("."):
        if isinstance(cur, list) and cur and isinstance(cur[0], dict):
            cur = cur[0]
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def extract(record: dict, spec) -> tuple[object, str | None]:
    """Return (value, alias_that_matched). Canonical name wins over aliases."""
    for key in [spec.name, *spec.aliases]:
        val = dig(record, key)
        if val is not None and spec.valid(val):
            return val, key
    return None, None


def parse_ts(value) -> datetime | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        secs = value / 1000 if value > 1e11 else value
        try:
            return datetime.fromtimestamp(secs, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        txt = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(txt)
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def check_timestamps(values: list) -> list[tuple[bool, str]]:
    """Plausibility checks on posted_at.

    A field that merely *parses* is not good enough: a scrape time or a
    date-rounded value both look fine until they silently destroy fixed-age
    comparison (plan section 5.4).
    """
    out = []
    parsed = [p for p in (parse_ts(v) for v in values) if p is not None]
    out.append((len(parsed) == len(values) and bool(values),
                "all {} timestamps parse ({} ok)".format(len(values), len(parsed))))
    if not parsed:
        out.append((False, "no parseable timestamps - cannot assess further"))
        return out

    now = datetime.now(timezone.utc)
    future = [p for p in parsed if p > now]
    out.append((not future,
                "none in the future ({} future-dated)".format(len(future))))

    distinct = len({p.isoformat() for p in parsed})
    out.append((distinct > max(1, len(parsed) // 10),
                "values vary ({} distinct of {}) - a near-constant value "
                "suggests a scrape time, not a post time".format(distinct, len(parsed))))

    # A dominant single value survives the distinctness check above when only
    # *part* of the sample is contaminated, e.g. half the rows carrying the
    # scrape date. Check the modal share directly.
    modal_ts, modal_n = Counter(p.isoformat() for p in parsed).most_common(1)[0]
    modal_share = modal_n / len(parsed)
    out.append((modal_share <= 0.20,
                "no dominant timestamp (most common value is {:.0%} of rows"
                "{}) - a spike at one value means scrape-time contamination"
                .format(modal_share,
                        ", at " + modal_ts if modal_share > 0.20 else "")))

    midnight = sum(1 for p in parsed if (p.hour, p.minute, p.second) == (0, 0, 0))
    out.append((midnight <= len(parsed) * 0.5,
                "time-of-day preserved ({}/{} at exactly 00:00:00) - "
                "date-rounded values break posting-time features".format(
                    midnight, len(parsed))))

    span = max(parsed) - min(parsed)
    out.append((span.days >= 7,
                "spans {} days - too narrow a window cannot support "
                "chronological validation".format(span.days)))

    ages = sorted((now - p).days for p in parsed)
    mature = sum(1 for a in ages if a >= 35)
    # Strategy A (plan 5.4) admits only videos at or past the anchor age. A
    # sample with none of them cannot build a fixed-age training corpus,
    # however complete its fields are.
    out.append((mature > 0,
                "age range {}-{} days; {}/{} are >=35d and so eligible for "
                "Strategy A mature snapshots".format(
                    ages[0], ages[-1], mature, len(ages))))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 0A provider scorecard")
    ap.add_argument("sample", type=Path, help="provider output: JSON or JSONL")
    ap.add_argument("--provider", default="(unnamed)")
    args = ap.parse_args()

    try:
        records = load_records(args.sample)
    except (OSError, json.JSONDecodeError) as exc:
        print("FAILED to read {}: {}".format(args.sample, exc), file=sys.stderr)
        return 2
    if not records:
        print("FAILED: no records found in {}".format(args.sample), file=sys.stderr)
        return 2

    n = len(records)
    print("\nPhase 0A scorecard - {}".format(args.provider))
    print("sample: {}  records: {}  scored: {:%Y-%m-%d %H:%M} UTC".format(
        args.sample.name, n, datetime.now(timezone.utc)))

    rows, alias_used, ts_values = [], {}, []
    for spec in REQUIREMENTS:
        hits, matched = 0, Counter()
        for rec in records:
            val, alias = extract(rec, spec)
            if alias is not None:
                hits += 1
                matched[alias] += 1
                if spec.name == "posted_at":
                    ts_values.append(val)
        rows.append((spec, hits / n))
        if matched:
            alias_used[spec.name] = matched.most_common(1)[0][0]

    width = max(len(s.name) for s, _ in rows) + 2
    for level in (BLOCKING, IMPORTANT, NICE, CAPABILITY):
        group = [(s, r) for s, r in rows if s.criticality == level]
        if not group:
            continue
        print("\n  {}".format(level))
        for spec, rate in group:
            need = 1.0 if level == BLOCKING else 0.0
            mark = "FAIL" if rate < need else ("ok  " if rate > 0 else "----")
            via = alias_used.get(spec.name)
            via = "  via '{}'".format(via) if via and via != spec.name else ""
            print("    [{}] {:<{w}} {:6.1%}{}".format(
                mark, spec.name, rate, via, w=width))

    scored = [(s, r) for s, r in rows if s.criticality in (BLOCKING, IMPORTANT)]
    completeness = sum(r for _, r in scored) / len(scored)
    blocking_missing = [s.name for s, r in rows
                        if s.criticality == BLOCKING and r < 1.0]

    print("\n  TIMESTAMP PLAUSIBILITY (posted_at)")
    ts_results = check_timestamps(ts_values) if ts_values else [
        (False, "posted_at absent - fixed-age comparison impossible")]
    for ok, msg in ts_results:
        print("    [{}] {}".format("ok  " if ok else "FAIL", msg))
    ts_ok = all(ok for ok, _ in ts_results)

    # Metric precision. Two providers can both score 100% on field presence
    # while one reports exact counts and the other expands the abbreviated
    # figure shown on screen ("54.9M" -> 54900000). Presence checks cannot
    # see that, so check significant digits directly.
    view_spec = next(s for s in REQUIREMENTS if s.name == "views")
    vals = []
    for rec in records:
        v, _ = extract(rec, view_spec)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            vals.append(int(v))
    big = [v for v in vals if v >= 10000]
    print("")
    print("  METRIC PRECISION (views)")
    if not big:
        print("    [ -- ] no values >=10,000 in sample; cannot assess")
    else:
        rounded = sum(1 for v in big if len(str(v).rstrip("0")) <= 3)
        share = rounded / len(big)
        print("    [{}] {}/{} values >=10k carry <=3 significant digits ({:.0%})"
              .format("warn" if share > 0.5 else "ok  ", rounded, len(big), share))
        print("          {}".format(
            "provider appears to expand displayed/abbreviated counts - "
            "fine for a log-ratio target, but small within-creator "
            "differences will be unresolvable" if share > 0.5 else
            "counts look exact"))

    media = next((r for s, r in rows if s.name == "media_url"), 0.0)
    print("\n  TIER 2/3 FEASIBILITY (reported, not scored)")
    verdict = ("media-bearing: third-party multimodal analysis is possible"
               if media >= 0.5 else
               "metadata-only: third-party videos cannot be analysed "
               "multimodally from this provider")
    print("    media_url present on {:.1%} of records -> {}".format(media, verdict))

    print("\n  GATE")
    missing_txt = ("missing: " + ", ".join(blocking_missing)
                   if blocking_missing else "none missing")
    cond = [
        (not blocking_missing,
         "all BLOCKING fields at 100% ({})".format(missing_txt)),
        (completeness >= COMPLETENESS_THRESHOLD,
         "BLOCKING+IMPORTANT completeness {:.1%} >= {:.0%}".format(
             completeness, COMPLETENESS_THRESHOLD)),
        (ts_ok, "timestamp plausibility checks pass"),
    ]
    for ok, msg in cond:
        print("    [{}] {}".format("PASS" if ok else "FAIL", msg))
    print("    [ -- ] measured cost within projection  (record manually)")
    print("    [ -- ] provider ToS reviewed vs plan 2.3 (record manually)")

    passed = all(ok for ok, _ in cond)
    print("\n  AUTOMATED VERDICT: {}  (cost and ToS conditions are manual; "
          "see 0A report)\n".format("PASS" if passed else "FAIL"))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
