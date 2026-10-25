"""Serve local master files to the ViralLab web UI. Runs on the WORKSTATION.

Why this exists: the unified interface is served by the always-on Ubuntu
server, which holds the authoritative database and the preserved Instagram
media. Local master files live on `D:` on this machine and are deliberately
never transferred (ADR 0006). So the server cannot play a master, and the
review view would otherwise show metadata only.

This is the smallest thing that closes that gap: a read-only, loopback-only
file server that the UI probes for and uses when it is running. It replaces
the review workflow that used to live in `scripts/review_ui.py` - the
decisions now happen in the web UI, and all this does is hand over bytes.

    python scripts/local_media_bridge.py
    # then the Review view in the web UI gains master playback

Security posture:
  * binds 127.0.0.1 only, so nothing off this machine can reach it
  * serves a file ONLY if its absolute path is recorded in `local_masters`,
    so a crafted path cannot read arbitrary disk
  * read-only: no endpoint changes anything
  * CORS is allowed for the configured UI origin only
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dbq import query  # noqa: E402
from remote import connect, env  # noqa: E402

PORT = 8770

#: Absolute paths this bridge is willing to serve, loaded once from the
#: authoritative database. A path that is not a recorded local master is not
#: served, whatever the request says - which is what makes an arbitrary-file
#: read impossible rather than merely unlikely.
_allowed: set[str] = set()
_allowed_lock = threading.Lock()


def load_allowed() -> set[str]:
    c = connect()
    try:
        rows = query(c, "select path from local_masters where path is not null")
    finally:
        c.close()
    out = set()
    for r in rows:
        try:
            out.add(str(Path(r["path"]).resolve()))
        except OSError:
            continue
    return out


def ui_origin() -> str:
    e = env()
    host = e.get("UBUNTU_HOST", "").strip()
    return f"https://{host}:8443" if host else "*"


def serve_range(handler: BaseHTTPRequestHandler, path: Path) -> None:
    """Send a file, honouring one Range header so video can seek."""
    size = path.stat().st_size
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    start, end, partial = 0, size - 1, False
    rng = handler.headers.get("Range")
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
    handler.send_header("Access-Control-Allow-Origin", handler.allow_origin)
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


class Handler(BaseHTTPRequestHandler):
    server_version = "ViralLabBridge/1"
    allow_origin = "*"

    def log_message(self, fmt, *a):          # quiet; this is a local tool
        pass

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", self.allow_origin)
        self.send_header("Access-Control-Allow-Headers", "Range")
        self.send_header("Access-Control-Expose-Headers",
                         "Content-Range, Accept-Ranges, Content-Length")

    def do_OPTIONS(self):                                    # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):                                        # noqa: N802
        u = urlparse(self.path)
        if u.path == "/bridge/health":
            body = json.dumps({"status": "ok",
                               "masters": len(_allowed)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)
            return

        if u.path == "/bridge/master":
            wanted = (parse_qs(u.query).get("path") or [""])[0]
            try:
                resolved = str(Path(wanted).resolve())
            except OSError:
                resolved = ""
            with _allowed_lock:
                permitted = resolved in _allowed
            if not permitted:
                # Deliberately identical response whether the path is
                # unknown or merely absent: the bridge does not confirm what
                # exists on disk.
                self.send_response(404)
                self._cors()
                self.end_headers()
                return
            p = Path(resolved)
            if not p.is_file():
                self.send_response(410)
                self._cors()
                self.end_headers()
                return
            serve_range(self, p)
            return

        self.send_response(404)
        self._cors()
        self.end_headers()


def main() -> int:
    global _allowed
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--origin", default=None,
                    help="UI origin allowed to read from this bridge")
    args = ap.parse_args()

    print("  loading the allowed master paths from the server...")
    try:
        _allowed = load_allowed()
    except Exception as exc:                                 # noqa: BLE001
        print(f"  could not reach the database: {exc}")
        return 1
    present = sum(1 for p in _allowed if Path(p).is_file())
    print(f"  {len(_allowed)} master path(s) recorded, {present} present on "
          f"this machine")
    if not present:
        print("  nothing to serve from here; is this the workstation with D:?")

    Handler.allow_origin = args.origin or ui_origin()
    print(f"  allowing requests from {Handler.allow_origin}")
    print(f"\n  bridge on http://127.0.0.1:{args.port}"
          f"\n  leave this running and the web UI gains master playback."
          f"\n  Ctrl-C to stop.")
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
