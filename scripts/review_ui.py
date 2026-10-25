"""Side-by-side review of proposed content relationships.

**SUPERSEDED.** Reviewing now happens in the unified web interface served by
the Ubuntu API (ADR 0006), and local master playback is handled by
`scripts/local_media_bridge.py`, which is this file's serving half without
the duplicated review workflow.

Kept, not deleted, because it still works offline and needs no server: if
the Ubuntu box is unreachable this is the fallback. Nothing is lost by using
either - every decision is stored in the database, attributed to `human`,
and neither tool overwrites the other.

    python scripts/local_media_bridge.py    # preferred: feeds the web UI
    python scripts/review_ui.py             # fallback, standalone

Why a local web app rather than a page or a terminal prompt: the two things
that need comparing live in different places. The local master sits on `D:`
on this machine and the published Instagram clip sits on the Ubuntu server.
Only something running here can read the first and fetch the second, and
only a browser can play them both at once.

Design decisions that matter:

  * **Every decision is written to the database immediately**, attributed to
    `human` with a timestamp. Closing the tab loses nothing and the queue
    resumes exactly where it was. Reviewing is never a prerequisite for
    other work continuing - that is the point of the queue existing.
  * **Four answers, not two.** Besides yes/no there is *skip* (decide later)
    and *different edit* -> `status='variant'`, which records "same
    performance, not this export". That answer previously had nowhere to go,
    so a real relationship had to be recorded as a falsehood either way.
  * **The evidence is shown, not just the score.** Which audio method was
    used, how much audio the score rests on, the offset, the duration delta,
    the tied variants. A score with no provenance is not reviewable.
  * **Instagram media is cached locally on first view** and reused, so
    stepping back and forth through the queue does not re-download.

No new dependencies: stdlib http.server, with Range support added by hand
because video seeking needs it.
"""

from __future__ import annotations

import argparse
import html
import json
import mimetypes
import os
import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dbq import execute as db_execute, query as db_query  # noqa: E402
from remote import connect  # noqa: E402

REMOTE = "/home/rastlyn/virallab"
CACHE = Path(os.environ.get("TEMP", ".")) / "virallab_review_cache"
PORT = 8770

_lock = threading.Lock()
_conn = None


def conn():
    """One SSH connection, reused. Reconnects if the server dropped it."""
    global _conn
    with _lock:
        if _conn is None or not _conn.get_transport() \
                or not _conn.get_transport().is_active():
            _conn = connect()
        return _conn


# --------------------------------------------------------------------------
# queries
# --------------------------------------------------------------------------

PENDING_LINKS = """
select l.id, l.score, l.frame_overlap, l.duration_delta_s, l.runner_up,
       l.audio_similarity, l.audio_method, l.audio_offset_s, l.evidence,
       l.status, l.notes,
       v.id as video_id, v.platform, v.external_id, v.caption, v.posted_at,
       v.duration_s as post_duration, v.media_status,
       m.id as master_id, m.quick_id, m.filename, m.path,
       m.duration_s as master_duration,
       p.title as performance
from video_master_links l
join videos v on v.id = l.video_id
join local_masters m on m.id = l.master_id
left join performances p on p.id = m.performance_id
where l.status = 'pending'
order by l.score desc
"""

PENDING_PAIRS = """
select pl.id, pl.score, pl.evidence, pl.relationship_type, pl.status,
       a.id as a_id, a.platform as a_platform, a.external_id as a_ext,
       a.caption as a_caption, a.posted_at as a_posted,
       a.duration_s as a_duration, a.cover_url as a_cover,
       b.id as b_id, b.platform as b_platform, b.external_id as b_ext,
       b.caption as b_caption, b.posted_at as b_posted,
       b.duration_s as b_duration, b.cover_url as b_cover
from publication_links pl
join videos a on a.id = pl.video_a_id
join videos b on b.id = pl.video_b_id
where pl.status = 'proposed'
order by pl.score desc
"""

COUNTS = """
select 'master_' || status as k, count(*) as n
  from video_master_links group by status
union all
select 'pair_' || status as k, count(*) as n
  from publication_links group by status
"""


def fetch(sql: str) -> list[dict]:
    return db_query(conn(), sql)


# --------------------------------------------------------------------------
# media
# --------------------------------------------------------------------------

def ig_media(external_id: str) -> Path | None:
    """Instagram clip, cached locally after the first fetch."""
    CACHE.mkdir(parents=True, exist_ok=True)
    local = CACHE / f"ig_{external_id}.mp4"
    if local.exists() and local.stat().st_size > 0:
        return local
    c = conn()
    sftp = c.open_sftp()
    try:
        remote = f"{REMOTE}/media/ig_{external_id}.mp4"
        try:
            sftp.stat(remote)
        except IOError:
            return None
        tmp = local.with_suffix(".part")
        sftp.get(remote, str(tmp))
        tmp.replace(local)
        return local
    finally:
        sftp.close()


def serve_file(handler: BaseHTTPRequestHandler, path: Path) -> None:
    """Send a file, honouring a single Range request so video can seek."""
    size = path.stat().st_size
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    rng = handler.headers.get("Range")
    start, end = 0, size - 1
    partial = False
    if rng:
        m = re.match(r"bytes=(\d*)-(\d*)", rng)
        if m:
            if m.group(1):
                start = int(m.group(1))
            if m.group(2):
                end = min(int(m.group(2)), size - 1)
            partial = True
    if start >= size:
        handler.send_response(416)
        handler.send_header("Content-Range", f"bytes */{size}")
        handler.end_headers()
        return
    length = end - start + 1
    handler.send_response(206 if partial else 200)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Content-Length", str(length))
    if partial:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    handler.end_headers()
    with path.open("rb") as fh:
        fh.seek(start)
        remaining = length
        while remaining > 0:
            chunk = fh.read(min(262144, remaining))
            if not chunk:
                break
            try:
                handler.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                return
            remaining -= len(chunk)


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

CSS = """
:root{--bg:#faf9f7;--fg:#1d1b18;--mut:#6b655c;--line:#e3dfd8;--card:#fff;
--ok:#1f7a4d;--no:#a3321f;--var:#8a5a00;--skip:#4a5568;--accent:#2b5c8a}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
--bg:#16150f;--fg:#f0ece4;--mut:#9c958a;--line:#2e2b24;--card:#1f1e17;
--ok:#5fd39a;--no:#f08a72;--var:#e0b155;--skip:#9aa5b1;--accent:#7db6e8}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif}
header{padding:16px;border-bottom:1px solid var(--line);
display:flex;gap:16px;align-items:baseline;flex-wrap:wrap}
h1{font-size:17px;margin:0;font-weight:650}
.wrap{max-width:1180px;margin:0 auto;padding:16px}
.pill{font-size:12px;color:var(--mut);border:1px solid var(--line);
border-radius:999px;padding:2px 9px;white-space:nowrap}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media (max-width:760px){.grid{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:14px}
.card h3{margin:0 0 8px;font-size:13px;text-transform:uppercase;
letter-spacing:.06em;color:var(--mut);font-weight:650}
video,img.cover{width:100%;border-radius:7px;background:#000;display:block}
dl{display:grid;grid-template-columns:auto 1fr;gap:3px 12px;margin:10px 0 0;
font-size:13px}
dt{color:var(--mut)}
dd{margin:0;font-variant-numeric:tabular-nums}
.cap{font-size:13px;color:var(--mut);margin-top:10px;white-space:pre-wrap;
max-height:7.5em;overflow:auto}
.ev{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:14px;margin:14px 0}
.ev table{width:100%;border-collapse:collapse;font-size:13px}
.ev td{padding:3px 8px 3px 0;vertical-align:top}
.ev td:first-child{color:var(--mut);white-space:nowrap;width:1%}
.actions{display:flex;gap:10px;flex-wrap:wrap;margin:16px 0}
button{font:inherit;font-weight:600;padding:10px 16px;border-radius:8px;
border:1px solid var(--line);background:var(--card);color:var(--fg);
cursor:pointer}
button:hover{border-color:var(--accent)}
button.ok{color:var(--ok)}button.no{color:var(--no)}
button.var{color:var(--var)}button.skip{color:var(--skip)}
a{color:var(--accent)}
.warn{border-left:3px solid var(--var);padding-left:10px;font-size:13px;
color:var(--mut);margin:12px 0}
.empty{text-align:center;color:var(--mut);padding:50px 16px}
kbd{font:12px ui-monospace,monospace;border:1px solid var(--line);
border-radius:4px;padding:1px 5px;color:var(--mut)}
table.queue{width:100%;border-collapse:collapse;font-size:13px}
table.queue th{text-align:left;color:var(--mut);font-weight:600;
border-bottom:1px solid var(--line);padding:6px 8px}
table.queue td{padding:6px 8px;border-bottom:1px solid var(--line)}
table.queue tr:hover td{background:var(--bg)}
"""


def page(title: str, body: str) -> bytes:
    return (f"""<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>{CSS}</style>
{body}</html>""").encode("utf-8")


def fnum(v, nd=2, dash="-"):
    return dash if v is None else f"{float(v):.{nd}f}"


def evidence_table(rows: list[tuple]) -> str:
    cells = "".join(
        f"<tr><td>{html.escape(str(k))}</td><td>{v}</td></tr>"
        for k, v in rows if v not in (None, ""))
    return f"<div class=ev><table>{cells}</table></div>"


def master_card(r: dict) -> str:
    p = Path(r["path"] or "")
    exists = p.exists()
    player = (f"<video controls preload=metadata "
              f"src='/media/master/{html.escape(r['quick_id'])}'></video>"
              if exists else
              "<div class=warn>File not found at the recorded path, so it "
              "cannot be played here. The decision can still be made from "
              "the evidence below.</div>")
    return f"""<div class=card><h3>Local master</h3>{player}
<dl><dt>file</dt><dd>{html.escape(r['filename'] or '')}</dd>
<dt>duration</dt><dd>{fnum(r['master_duration'],2)} s</dd>
<dt>performance</dt><dd>{html.escape(r['performance'] or '(ungrouped)')}</dd>
<dt>path</dt><dd style="font-size:12px;color:var(--mut)">
{html.escape(str(p))}</dd></dl></div>"""


def post_card(r: dict) -> str:
    ext = r["external_id"]
    player = (f"<video controls preload=metadata "
              f"src='/media/ig/{html.escape(ext)}'></video>"
              if r["platform"] == "instagram" else
              "<div class=warn>TikTok returns no media file, so there is "
              "nothing to play.</div>")
    return f"""<div class=card><h3>{html.escape(r['platform'])} publication
</h3>{player}
<dl><dt>posted</dt><dd>{html.escape(str(r['posted_at'])[:16])}</dd>
<dt>duration</dt><dd>{fnum(r['post_duration'],2)} s</dd>
<dt>id</dt><dd style="font-size:12px">{html.escape(ext)}</dd></dl>
<div class=cap>{html.escape(r['caption'] or '(no caption)')}</div></div>"""


def render_master_review(r: dict, pos: int, total: int) -> bytes:
    ev = r["evidence"] or {}
    if isinstance(ev, str):
        try:
            ev = json.loads(ev)
        except ValueError:
            ev = {}
    tied = ev.get("tied_variants") or []
    alts = ev.get("alternatives") or []
    method = r["audio_method"] or "none"
    warn = ""
    if method != "spectral":
        warn = ("<div class=warn>Audio agreement here came from the "
                f"<b>{html.escape(method)}</b> descriptor, which was measured "
                "and found unable to separate a different-hook cross-post "
                "from an unrelated performance. Treat the score as weak "
                "evidence and judge from the video.</div>")
    if ev.get("variant_ambiguous"):
        warn += ("<div class=warn>The <b>performance</b> is confident; only "
                 "which export was used is open. If the clip is this "
                 "performance but a different export, choose "
                 "<b>different edit</b>.</div>")

    rows = [
        ("combined score", f"<b>{fnum(r['score'],3)}</b>"),
        ("audio agreement", f"{fnum(r['audio_similarity'],3)} "
                            f"<span class=pill>{html.escape(method)}</span>"),
        ("audio coverage", fnum(ev.get("coverage"), 2)),
        ("audio offset", (f"{fnum(r['audio_offset_s'],2)} s "
                          "<span class=pill>a nonzero offset means a trimmed "
                          "head or a different hook</span>")
         if r["audio_offset_s"] else fnum(r["audio_offset_s"], 2)),
        ("frame overlap", fnum(r["frame_overlap"], 3)),
        ("duration delta", f"{fnum(r['duration_delta_s'],2)} s"),
        ("same cut", str(ev.get("same_cut"))),
        ("runner-up", fnum(r["runner_up"], 3)),
        ("tied variants", html.escape(", ".join(tied)) if tied else None),
        ("other candidates",
         html.escape(", ".join(f"{a.get('name')} ({a.get('score')})"
                               for a in alts)) if alts else None),
    ]
    body = f"""<header><h1>Is this the source of this post?</h1>
<span class=pill>{pos} of {total} pending</span>
<span class=pill>link #{r['id']}</span>
<a href="/" style="margin-left:auto">queue</a></header>
<div class=wrap>{warn}
<div class=grid>{master_card(r)}{post_card(r)}</div>
{evidence_table(rows)}
<form method=POST action=/decide>
<input type=hidden name=kind value=master>
<input type=hidden name=id value="{r['id']}">
<div class=actions>
<button class=ok name=answer value=confirmed>Yes, same video</button>
<button class=no name=answer value=rejected>No, not a match</button>
<button class=var name=answer value=variant>Same performance,
different edit</button>
<button class=skip name=answer value=skip>Skip for now</button>
</div></form>
<p style="font-size:13px;color:var(--mut)">Decisions save immediately. Close
the tab whenever you like and the queue resumes here.</p>
</div>"""
    return page("Review match", body)


def render_pair_review(r: dict, pos: int, total: int) -> bytes:
    ev = r["evidence"] or {}
    if isinstance(ev, str):
        try:
            ev = json.loads(ev)
        except ValueError:
            ev = {}
    sig = ev.get("signals") or {}

    def side(tag: str) -> str:
        plat = r[f"{tag}_platform"]
        ext = r[f"{tag}_ext"]
        if plat == "instagram":
            media = (f"<video controls preload=metadata "
                     f"src='/media/ig/{html.escape(ext)}'></video>")
        else:
            cover = r[f"{tag}_cover"]
            media = (f"<img class=cover src='{html.escape(cover)}' "
                     f"alt='TikTok cover'>" if cover else
                     "<div class=warn>No cover available.</div>")
            media += ("<div class=warn>TikTok returns no media file. This is "
                      "the cover image only - it was measured and found "
                      "unable to identify the video, so it is shown for "
                      "orientation, not as evidence.</div>")
        return f"""<div class=card><h3>{html.escape(plat)}</h3>{media}
<dl><dt>posted</dt><dd>{html.escape(str(r[f'{tag}_posted'])[:16])}</dd>
<dt>duration</dt><dd>{fnum(r[f'{tag}_duration'],2)} s</dd>
<dt>id</dt><dd style="font-size:12px">{html.escape(ext)}</dd></dl>
<div class=cap>{html.escape(r[f'{tag}_caption'] or '(no caption)')}</div>
</div>"""

    rows = [
        ("tier", f"<b>{html.escape(str(ev.get('tier','?')))}</b>"),
        ("combined score", fnum(r["score"], 3)),
        ("agreeing signals", f"{ev.get('agreeing','?')} of 3"),
        ("duration", f"{fnum(sig.get('duration'),2)} "
                     f"(delta {fnum(ev.get('duration_delta_s'),2)} s)"),
        ("publication date", f"{fnum(sig.get('date'),2)} "
                             f"(gap {ev.get('day_gap')} day(s))"),
        ("caption / hashtags", f"{fnum(sig.get('caption'),2)} "
                               f"(tokens {fnum(ev.get('token_jaccard'),2)}, "
                               f"tags {fnum(ev.get('hashtag_jaccard'),2)})"),
        ("margin over runner-up", fnum(ev.get("margin"), 2)),
        ("Instagram master", html.escape(str(ev.get("instagram_master") or ""))
         or None),
        ("performance", html.escape(str(ev.get("instagram_performance") or ""))
         or None),
    ]
    warn = ("<div class=warn>This proposal rests on <b>metadata only</b> - "
            "duration, date and caption. There is no TikTok media to compare, "
            "so nothing here can establish identity on its own. It is "
            "proposed for your judgement, not asserted.</div>")
    if (ev.get("margin") or 1) < 0.10:
        warn += ("<div class=warn>The runner-up scored almost as well, so "
                 "another publication fits this one nearly as "
                 "convincingly.</div>")
    body = f"""<header><h1>Are these the same video on both platforms?</h1>
<span class=pill>{pos} of {total} proposed</span>
<span class=pill>pair #{r['id']}</span>
<a href="/" style="margin-left:auto">queue</a></header>
<div class=wrap>{warn}
<div class=grid>{side('a')}{side('b')}</div>
{evidence_table(rows)}
<form method=POST action=/decide>
<input type=hidden name=kind value=pair>
<input type=hidden name=id value="{r['id']}">
<div class=actions>
<button class=ok name=answer value=confirmed>Yes, same video</button>
<button class=var name=answer value=platform_edit>Same performance,
different edit per platform</button>
<button class=no name=answer value=rejected>No, different videos</button>
<button class=skip name=answer value=skip>Skip for now</button>
</div></form></div>"""
    return page("Review cross-platform pair", body)


def render_queue() -> bytes:
    counts = {r["k"]: r["n"] for r in fetch(COUNTS)}
    links = fetch(PENDING_LINKS)
    pairs = fetch(PENDING_PAIRS)

    def pills() -> str:
        order = [("master_auto", "auto-linked"),
                 ("master_confirmed", "confirmed"),
                 ("master_variant", "variant"),
                 ("master_rejected", "rejected"),
                 ("master_pending", "pending"),
                 ("pair_proposed", "pairs proposed"),
                 ("pair_confirmed", "pairs confirmed"),
                 ("pair_rejected", "pairs rejected")]
        return "".join(f"<span class=pill>{label}: {counts.get(k,0)}</span>"
                       for k, label in order if counts.get(k))

    rows_l = "".join(
        f"""<tr><td><a href="/review/master/{r['id']}">{fnum(r['score'],2)}
</a></td><td>{fnum(r['audio_similarity'],2)}
<span class=pill>{html.escape(r['audio_method'] or 'none')}</span></td>
<td>{fnum(r['frame_overlap'],2)}</td>
<td>{html.escape((r['filename'] or '')[:40])}</td>
<td>{html.escape(r['platform'])} {html.escape(str(r['posted_at'])[:10])}</td>
</tr>""" for r in links)
    rows_p = "".join(
        f"""<tr><td><a href="/review/pair/{r['id']}">{fnum(r['score'],2)}
</a></td><td>{html.escape(str((r['evidence'] or {}).get('tier','?')))}</td>
<td>{html.escape(str(r['a_posted'])[:10])} &rarr;
{html.escape(str(r['b_posted'])[:10])}</td>
<td>{html.escape(str((r['evidence'] or {}).get('instagram_master') or '-')
                 [:36])}</td></tr>""" for r in pairs)

    body = f"""<header><h1>ViralLab &mdash; relationship review</h1>{pills()}
</header><div class=wrap>
<p style="color:var(--mut);font-size:13px">Nothing here blocks other work.
Decide as many or as few as you like; the queue remembers.</p>

<h2 style="font-size:15px">Master &rarr; publication ({len(links)})</h2>
{"<table class=queue><tr><th>score<th>audio<th>frames<th>local file"
 "<th>publication</tr>" + rows_l + "</table>" if links else
 "<div class=empty>Nothing pending.</div>"}

<h2 style="font-size:15px;margin-top:28px">Cross-platform pairs
({len(pairs)})</h2>
{"<table class=queue><tr><th>score<th>tier<th>dates<th>via master</tr>"
 + rows_p + "</table>" if pairs else
 "<div class=empty>Nothing proposed.</div>"}
</div>"""
    return page("ViralLab review", body)


# --------------------------------------------------------------------------
# decisions
# --------------------------------------------------------------------------

MASTER_ANSWERS = {"confirmed", "rejected", "variant"}
PAIR_ANSWERS = {"confirmed", "rejected"}


def decide(kind: str, ident: int, answer: str) -> None:
    if answer == "skip":
        return
    c = conn()
    if kind == "master":
        if answer not in MASTER_ANSWERS:
            return
        db_execute(c, f"""
            update video_master_links
               set status = '{answer}', decided_by = 'human',
                   decided_at = now()
             where id = {int(ident)}
        """.strip())
    else:
        if answer == "platform_edit":
            db_execute(c, f"""
                update publication_links
                   set status = 'confirmed',
                       relationship_type = 'platform_edit',
                       decided_by = 'human', decided_at = now()
                 where id = {int(ident)}
            """.strip())
            return
        if answer not in PAIR_ANSWERS:
            return
        db_execute(c, f"""
            update publication_links
               set status = '{answer}', decided_by = 'human',
                   decided_at = now()
             where id = {int(ident)}
        """.strip())


class Handler(BaseHTTPRequestHandler):
    server_version = "ViralLabReview/1"

    def log_message(self, fmt, *a):            # quiet; this is a local tool
        pass

    def _send(self, body: bytes, code=200, ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):                                        # noqa: N802
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        try:
            if not parts:
                return self._send(render_queue())

            if parts[0] == "media" and len(parts) == 3:
                if parts[1] == "master":
                    rows = fetch("select path from local_masters where "
                                 f"quick_id = '{parts[2][:64]}'")
                    p = Path(rows[0]["path"]) if rows else None
                    if p and p.exists():
                        return serve_file(self, p)
                    return self._send(b"not found", 404, "text/plain")
                if parts[1] == "ig":
                    ext = re.sub(r"[^0-9A-Za-z_]", "", parts[2])
                    p = ig_media(ext)
                    if p:
                        return serve_file(self, p)
                    return self._send(b"no media stored", 404, "text/plain")

            if parts[0] == "review" and len(parts) == 3:
                kind, ident = parts[1], int(parts[2])
                if kind == "master":
                    rows = fetch(PENDING_LINKS)
                    hit = [r for r in rows if r["id"] == ident]
                    if not hit:
                        return self._send(render_queue())
                    pos = rows.index(hit[0]) + 1
                    return self._send(
                        render_master_review(hit[0], pos, len(rows)))
                if kind == "pair":
                    rows = fetch(PENDING_PAIRS)
                    hit = [r for r in rows if r["id"] == ident]
                    if not hit:
                        return self._send(render_queue())
                    pos = rows.index(hit[0]) + 1
                    return self._send(
                        render_pair_review(hit[0], pos, len(rows)))

            return self._send(b"not found", 404, "text/plain")
        except Exception as exc:                             # noqa: BLE001
            return self._send(
                page("Error", f"<div class=wrap><h1>Something failed</h1>"
                              f"<pre>{html.escape(str(exc))}</pre>"
                              f"<a href='/'>back to the queue</a></div>"),
                500)

    def do_POST(self):                                       # noqa: N802
        if urlparse(self.path).path != "/decide":
            return self._send(b"not found", 404, "text/plain")
        n = int(self.headers.get("Content-Length") or 0)
        form = parse_qs(self.rfile.read(n).decode("utf-8"))
        kind = (form.get("kind") or [""])[0]
        answer = (form.get("answer") or [""])[0]
        try:
            ident = int((form.get("id") or ["0"])[0])
        except ValueError:
            ident = 0
        try:
            decide(kind, ident, answer)
        except Exception as exc:                             # noqa: BLE001
            return self._send(
                page("Error", f"<div class=wrap><h1>Could not save</h1>"
                              f"<pre>{html.escape(str(exc))}</pre>"
                              f"<a href='/'>back</a></div>"), 500)
        # Straight to the next item, so a run of decisions needs no detour.
        rows = fetch(PENDING_LINKS if kind == "master" else PENDING_PAIRS)
        nxt = (f"/review/{kind}/{rows[0]['id']}" if rows else "/")
        self.send_response(303)
        self.send_header("Location", nxt)
        self.end_headers()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    counts = {r["k"]: r["n"] for r in fetch(COUNTS)}
    print(f"  pending master links : {counts.get('master_pending', 0)}")
    print(f"  proposed pairs       : {counts.get('pair_proposed', 0)}")
    url = f"http://127.0.0.1:{args.port}/"
    print(f"\n  review at {url}\n  Ctrl-C to stop; decisions are already "
          f"saved as you go.")
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
