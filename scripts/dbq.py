"""Query the authoritative database from the Windows side, losslessly.

Use this instead of parsing `sql.py --query` output. That older path printed
`repr(rows)` and the caller ran `ast.literal_eval` on it, which silently
truncated every long string - SQLAlchemy's Row repr replaces the middle of a
long value with `... (N characters truncated) ...`. Captions, notes and
evidence blobs all came back corrupted.

    from dbq import query, execute
    rows = query(c, "select external_id, caption from videos")
    rows[0]["caption"]          # full text, dicts not tuples
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from remote import run

REMOTE = "/home/rastlyn/virallab"
_UPLOADED: set[int] = set()


def _ensure_helper(c) -> None:
    """Upload jsonq.py once per connection."""
    if id(c) in _UPLOADED:
        return
    sftp = c.open_sftp()
    try:
        sftp.put(str(Path(__file__).resolve().parent / "provision"
                     / "jsonq.py"), f"{REMOTE}/jsonq.py")
    finally:
        sftp.close()
    _UPLOADED.add(id(c))


def _run(c, sql: str) -> dict:
    _ensure_helper(c)
    tag = uuid.uuid4().hex[:12]
    sqlf, outf = f"{REMOTE}/.q_{tag}.sql", f"{REMOTE}/.q_{tag}.json"
    sftp = c.open_sftp()
    try:
        # The statement travels as a file, so no shell quoting is involved
        # and a query containing quotes or newlines cannot be mangled.
        with sftp.open(sqlf, "w") as fh:
            fh.write(sql)
    finally:
        sftp.close()
    rc, out, err = run(
        c, f"cd {REMOTE} && set -a && . ./.env && set +a && "
           f".venv/bin/python jsonq.py {sqlf} {outf}", timeout=900)
    if rc != 0:
        run(c, f"rm -f {sqlf} {outf}", timeout=60)
        raise RuntimeError((err or out).strip()[:4000])
    sftp = c.open_sftp()
    try:
        with sftp.open(outf, "r") as fh:
            payload = json.loads(fh.read().decode("utf-8"))
    finally:
        sftp.close()
        run(c, f"rm -f {sqlf} {outf}", timeout=60)
    return payload


def query(c, sql: str) -> list[dict]:
    """Rows as dicts, with every value intact."""
    return _run(c, sql)["rows"]


def execute(c, sql: str) -> int:
    """Run a statement, return the affected row count."""
    return _run(c, sql)["rowcount"]
