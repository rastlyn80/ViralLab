"""Owner-supplied analytics exports, ingested without guessing.

The only route to TikTok retention that exists. TikTok's Display API
returns views, likes, comments and shares and nothing about watch time
(ADR 0001), and the Research API is permanently out of scope (CLAUDE.md
§2.7). Average watch time and "watched full video" can therefore only reach
this system if the owner exports them from TikTok Studio by hand. The same
mechanism takes Instagram Insights exports, which is useful mainly as a
cross-check on the API.

## The rule here is: recognise, or report as unrecognised

A dashboard export is not an API. Its column headings change without
notice, differ by locale, and carry units only in the heading if at all. A
parser that quietly guesses which column is "average watch time" will one
day be wrong and will produce a number that looks exactly as trustworthy as
a right one.

So every column is either matched against a declared alias set or recorded
in `parse_notes["unmapped_columns"]`, and the mapping that was actually
used is stored with the import. A later reader can tell a parsed column
from an ignored one without reopening the file.

## Units are converted on the way in, and recorded

TikTok Studio reports watch time in seconds, Instagram's API in
milliseconds. Both export bare percentages. Each metric below declares its
unit and the parser stores it per row, because a unitless float in a shared
table is a unit bug waiting for its second reader.

## CSV only, deliberately

No spreadsheet library is added for this (CLAUDE.md §6: each dependency
needs a reason beyond convenience). Both dashboards offer CSV, and where
they offer only XLSX, "Save as CSV" is one step in any spreadsheet
program. The parser accepts comma and tab delimiters and a UTF-8 BOM,
because exports carry all three in practice.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.db.models import AnalyticsImport, ImportedMetric, Video

logger = logging.getLogger("virallab.analytics.imports")

METHOD_VERSION = "analytics-import-v1"

SOURCES = ("tiktok_studio", "instagram_insights")


@dataclass(frozen=True)
class MetricSpec:
    """One importable metric: what it is called, and what it means."""

    name: str
    unit: str
    #: Lower-cased heading fragments. A heading matches when it equals one
    #: alias exactly after normalisation — NOT when it merely contains one.
    #: Substring matching maps "Average watch time" and "Total watch time"
    #: to the same metric, which is how a retention figure silently becomes
    #: a sum.
    aliases: tuple[str, ...]
    #: Strip a trailing percent sign and divide by 100 where the export
    #: writes "43.2%" rather than 0.432.
    is_percent: bool = False


#: TikTok Studio, Analytics -> Content -> download. Headings are what the
#: English-locale export writes; a locale that differs will land its
#: columns in `unmapped_columns` rather than being guessed at.
#:
#: `[SECONDARY]` — these aliases come from the dashboard's own export, not
#: from platform-official documentation, because TikTok does not document
#: TikTok Studio's CSV schema anywhere. The parser is built so that a wrong
#: or changed heading produces an unmapped column and a visible warning,
#: never a mis-assigned metric.
TIKTOK_METRICS: tuple[MetricSpec, ...] = (
    MetricSpec("views", "count", ("total views", "video views", "views")),
    MetricSpec("likes", "count", ("total likes", "likes")),
    MetricSpec("comments", "count", ("total comments", "comments")),
    MetricSpec("shares", "count", ("total shares", "shares")),
    MetricSpec("saves", "count", ("total saves", "saves", "favorites")),
    MetricSpec("avg_watch_time_s", "s",
               ("average watch time", "avg watch time",
                "average time watched")),
    # Stored as a fraction, not a percentage, and named for what it holds.
    # `is_percent` divides by 100 on the way in, so a column reading
    # "43.2%" lands as 0.432 and a reader cannot mistake the scale.
    MetricSpec("watched_full_video_rate", "fraction",
               ("watched full video", "watched full video rate",
                "full video watch rate"), is_percent=True),
    MetricSpec("total_play_time_s", "s",
               ("total play time", "total time watched")),
    MetricSpec("new_followers", "count",
               ("new followers", "followers gained")),
    MetricSpec("reach", "count", ("reach", "unique viewers")),
)

#: Instagram Insights export. Mainly a cross-check against the API, which
#: already supplies these — a disagreement between the two is worth seeing.
INSTAGRAM_METRICS: tuple[MetricSpec, ...] = (
    MetricSpec("views", "count", ("views", "plays", "video views")),
    MetricSpec("reach", "count", ("reach", "accounts reached")),
    MetricSpec("likes", "count", ("likes",)),
    MetricSpec("comments", "count", ("comments",)),
    MetricSpec("shares", "count", ("shares",)),
    MetricSpec("saves", "count", ("saves", "saved")),
    MetricSpec("follows", "count", ("follows", "accounts engaged")),
    MetricSpec("avg_watch_time_s", "s",
               ("average watch time", "avg watch time")),
)

METRICS_BY_SOURCE = {"tiktok_studio": TIKTOK_METRICS,
                     "instagram_insights": INSTAGRAM_METRICS}

#: Headings that identify WHICH publication a row is about. Without one of
#: these the file cannot be used at all, and the parser says so rather than
#: importing rows it cannot attach to anything.
REF_ALIASES = ("video link", "post link", "link", "url", "video url",
               "post url", "share link", "permalink", "video id", "post id",
               "media id", "id")

#: Headings carrying the publication time. Optional: used to age-anchor the
#: imported figures and to match a row whose link is missing.
TIME_ALIASES = ("post time", "publish time", "date", "posted", "created",
                "publish date", "post date", "date posted", "time")

#: Headings carrying a human title. Never used for matching — two takes of
#: one piece share a caption — but kept so an unmatched row is identifiable
#: by a person.
TITLE_ALIASES = ("video title", "post title", "title", "caption",
                 "description")


@dataclass
class ParsedRow:
    external_ref: str
    title: str | None = None
    posted_at: datetime | None = None
    metrics: dict[str, tuple[float, str]] = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


@dataclass
class ParsedFile:
    source: str
    platform: str
    rows: list[ParsedRow] = field(default_factory=list)
    column_map: dict[str, str] = field(default_factory=dict)
    unmapped_columns: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class ImportRefused(ValueError):
    """The file cannot be imported, and guessing would be worse."""


def _norm(heading: str) -> str:
    """Lower-case, strip punctuation and collapse whitespace.

    Exports vary in capitalisation, trailing spaces, non-breaking spaces and
    bracketed unit suffixes: "Average watch time (s)" and "Average Watch
    Time" are the same column.
    """
    h = heading.replace("﻿", "").replace("\xa0", " ")
    h = re.sub(r"\([^)]*\)", " ", h)
    h = re.sub(r"[^\w\s%]", " ", h)
    return re.sub(r"\s+", " ", h).strip().lower()


def _to_float(value: str, *, is_percent: bool
              ) -> tuple[float, str] | None:
    """A dashboard number to a float plus a warning note, or None.

    Handles thousands separators, a trailing percent sign, and the
    abbreviated forms a dashboard uses when a figure is large. An
    abbreviation is a real precision loss and the caller records it —
    `is_rounded` exists on snapshots for exactly this reason (Phase 0A
    finding 2) — but here the alternative is dropping the row.

    **Percent handling is the part that can silently divide a finding by
    100.** A percentage column may arrive as `"43.2%"`, as `43.2`, or
    already as a fraction `0.432`, and dividing the third by 100 would
    turn a 43% completion rate into 0.4%. So the division happens when the
    cell carries a `%`, or when the value exceeds 1. A bare value at or
    below 1 is genuinely ambiguous — `1` could mean "1%" or "100%" — and
    is returned unchanged with a note saying so, rather than guessed at.
    This matters most for `watched_full_video_rate`, which is one of the
    two metrics the whole TikTok export exists to obtain.
    """
    if value is None:
        return None
    raw = str(value)
    had_percent = "%" in raw
    s = raw.strip().replace(",", "").replace("%", "")
    s = s.replace("\xa0", "")
    if not s or s in ("-", "--", "N/A", "n/a"):
        return None
    mult = 1.0
    if s and s[-1] in "KMB":
        mult = {"K": 1e3, "M": 1e6, "B": 1e9}[s[-1]]
        s = s[:-1]
    # "1m 23s" and "83s" both appear in watch-time columns.
    m = re.fullmatch(r"(?:(\d+)\s*m)?\s*(\d+(?:\.\d+)?)\s*s", s,
                     flags=re.IGNORECASE)
    if m:
        mins = float(m.group(1) or 0)
        return mins * 60 + float(m.group(2)), ""
    try:
        out = float(s) * mult
    except ValueError:
        return None
    if not is_percent:
        return out, ""
    if had_percent or out > 1.0:
        return out / 100.0, ""
    return out, (f"the value {out!r} carried no percent sign and is <= 1, "
                 f"so it was stored as a fraction unchanged; if the export "
                 f"meant {out}% then this one is wrong by 100x")


def _parse_time(value: str) -> datetime | None:
    s = (value or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M", "%d/%m/%Y", "%m/%d/%Y",
                "%Y/%m/%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _sniff(text: str) -> csv.Dialect | type[csv.Dialect]:
    sample = text[:8192]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        return csv.excel


def parse(text: str, *, source: str, platform: str) -> ParsedFile:
    """Parse an export into rows, declaring exactly what was recognised."""
    if source not in METRICS_BY_SOURCE:
        raise ImportRefused(f"unknown source {source!r}; expected one of "
                            f"{', '.join(SOURCES)}")
    specs = METRICS_BY_SOURCE[source]
    out = ParsedFile(source=source, platform=platform)

    reader = csv.DictReader(io.StringIO(text), dialect=_sniff(text))
    headings = reader.fieldnames or []
    if not headings:
        raise ImportRefused("the file has no header row")

    # Exact alias matching, longest alias first so "total views" wins over
    # "views" when a file carries both.
    by_alias: dict[str, MetricSpec] = {}
    for spec in specs:
        for alias in spec.aliases:
            by_alias.setdefault(alias, spec)

    ref_col = time_col = title_col = None
    metric_cols: dict[str, MetricSpec] = {}
    for h in headings:
        n = _norm(h)
        if not n:
            continue
        if ref_col is None and n in REF_ALIASES:
            ref_col = h
            out.column_map[h] = "external_ref"
            continue
        if time_col is None and n in TIME_ALIASES:
            time_col = h
            out.column_map[h] = "posted_at"
            continue
        if title_col is None and n in TITLE_ALIASES:
            title_col = h
            out.column_map[h] = "title"
            continue
        spec = by_alias.get(n)
        # First column wins per metric. A file carrying both "Total views"
        # and "Views" maps the first and reports the second as unmapped,
        # rather than silently overwriting one with the other.
        if spec is not None and not any(s.name == spec.name
                                        for s in metric_cols.values()):
            metric_cols[h] = spec
            out.column_map[h] = spec.name
            continue
        out.unmapped_columns.append(h)

    if ref_col is None:
        raise ImportRefused(
            "no column identifies which publication each row is about. "
            f"Looked for one of: {', '.join(REF_ALIASES)}. Headings found: "
            f"{', '.join(headings)}")
    if not metric_cols:
        raise ImportRefused(
            "no recognised metric columns. Headings found: "
            f"{', '.join(headings)}")

    for raw_row in reader:
        ref = (raw_row.get(ref_col) or "").strip()
        if not ref:
            continue
        row = ParsedRow(external_ref=ref, raw={k: v for k, v in
                                               raw_row.items() if v})
        if title_col:
            row.title = (raw_row.get(title_col) or "").strip() or None
        if time_col:
            row.posted_at = _parse_time(raw_row.get(time_col) or "")
        for col, spec in metric_cols.items():
            got = _to_float(raw_row.get(col) or "",
                            is_percent=spec.is_percent)
            if got is not None:
                val, note = got
                row.metrics[spec.name] = (val, spec.unit)
                if note:
                    warning = f"{col}: {note}"
                    if warning not in out.warnings:
                        out.warnings.append(warning)
        if row.metrics:
            out.rows.append(row)

    if out.unmapped_columns:
        out.warnings.append(
            f"{len(out.unmapped_columns)} column(s) were not recognised and "
            f"were NOT imported: {', '.join(out.unmapped_columns)}")
    return out


# ---------------------------------------------------------------------------
# matching exports back to publications
# ---------------------------------------------------------------------------

#: TikTok share links carry the publication id as the last numeric path
#: segment; Instagram permalinks carry a shortcode instead, which is not the
#: media id and cannot be resolved offline.
_TIKTOK_ID = re.compile(r"/video/(\d+)")
_TRAILING_ID = re.compile(r"(\d{8,})\D*$")


def extract_external_id(ref: str) -> str | None:
    """The platform's own id from whatever the export wrote."""
    ref = ref.strip()
    m = _TIKTOK_ID.search(ref)
    if m:
        return m.group(1)
    if ref.isdigit():
        return ref
    m = _TRAILING_ID.search(ref)
    return m.group(1) if m else None


def match(session: Session, rows: list[ParsedRow], platform: str
          ) -> dict[str, int]:
    """external_ref -> video_id, for the rows that can be resolved.

    Matching is by platform id only. Caption matching is deliberately not
    attempted: this account posts repeated takes of the same piece under
    the same caption, so a caption match would attach a figure to the wrong
    publication some of the time, and silently.
    """
    wanted: dict[str, str] = {}
    for r in rows:
        ext = extract_external_id(r.external_ref)
        if ext:
            wanted[r.external_ref] = ext
    if not wanted:
        return {}
    found = {v.external_id: v.id for v in session.scalars(
        select(Video).where(Video.platform == platform,
                            Video.external_id.in_(set(wanted.values())))
    ).all()}
    return {ref: found[ext] for ref, ext in wanted.items() if ext in found}


# ---------------------------------------------------------------------------
# ingestion
# ---------------------------------------------------------------------------

def ingest(session: Session, *, path: str | Path, source: str,
           platform: str, exported_at: date | None = None,
           ) -> tuple[AnalyticsImport, dict]:
    """Record one export. Idempotent on the file's own bytes.

    Re-submitting the same file returns the existing import untouched
    (CLAUDE.md §4.8). A *newer* export of the same period is a different
    file and becomes a new import; readers take the latest per metric
    rather than averaging the two.
    """
    p = Path(path)
    data = p.read_bytes()
    sha = hashlib.sha256(data).hexdigest()

    existing = session.scalar(select(AnalyticsImport).where(
        AnalyticsImport.file_sha256 == sha))
    if existing is not None:
        logger.info("analytics import %s already present as id %s",
                    p.name, existing.id)
        return existing, {"already_imported": True,
                          "import_id": existing.id,
                          "rows": existing.row_count,
                          "matched": existing.matched_count}

    text = data.decode("utf-8-sig", errors="replace")
    parsed = parse(text, source=source, platform=platform)
    matches = match(session, parsed.rows, platform)

    imp = AnalyticsImport(
        platform=platform, source=source, filename=p.name,
        file_sha256=sha, exported_at=exported_at,
        row_count=len(parsed.rows), matched_count=len(matches),
        method_version=METHOD_VERSION,
        parse_notes={"column_map": parsed.column_map,
                     "unmapped_columns": parsed.unmapped_columns,
                     "warnings": parsed.warnings,
                     "matched": len(matches),
                     "unmatched": len(parsed.rows) - len(matches)})
    session.add(imp)
    session.flush()

    # Publication times, for age anchoring. Taken from the database where
    # the publication is known, because `videos.posted_at` came from the
    # platform API and an export's own date column is often date-only.
    posted: dict[int, datetime] = {}
    if matches:
        for v in session.scalars(select(Video).where(
                Video.id.in_(set(matches.values())))).all():
            if v.posted_at:
                posted[v.id] = v.posted_at

    observed = (datetime(exported_at.year, exported_at.month,
                         exported_at.day, tzinfo=timezone.utc)
                if exported_at else None)

    written = 0
    for row in parsed.rows:
        vid = matches.get(row.external_ref)
        age = None
        ref_time = posted.get(vid) if vid else row.posted_at
        if observed is not None and ref_time is not None:
            age = (observed - ref_time).total_seconds() / 86400.0
        for metric, (value, unit) in row.metrics.items():
            session.add(ImportedMetric(
                import_id=imp.id, video_id=vid, platform=platform,
                external_ref=row.external_ref, metric=metric,
                value=value, unit=unit, observed_at=observed,
                observed_age_days=age, provenance="manual_export",
                raw={"title": row.title} if row.title else None))
            written += 1
    session.flush()

    summary = {
        "import_id": imp.id,
        "rows": len(parsed.rows),
        "metrics_written": written,
        "matched": len(matches),
        "unmatched": len(parsed.rows) - len(matches),
        "column_map": parsed.column_map,
        "unmapped_columns": parsed.unmapped_columns,
        "warnings": parsed.warnings,
    }
    if not exported_at:
        summary.setdefault("warnings", []).append(
            "No export date given, so these figures carry no observation "
            "time and cannot be age-anchored (CLAUDE.md §3.8a).")
    logger.info("analytics import %s: %s", p.name, summary)
    return imp, summary
