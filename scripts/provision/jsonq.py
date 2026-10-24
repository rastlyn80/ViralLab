"""Run a query on the server and write the result as JSON. Runs ON Ubuntu.

Exists because the previous transport - `sql.py --query` printing
`repr(rows)` and the client calling `ast.literal_eval` - silently corrupted
data. SQLAlchemy's Row repr **truncates long strings**, inserting
`... (725 characters truncated) ...` into the middle of any caption over a
couple of hundred characters. That is invisible until something depends on
the text: the cross-platform matcher's caption signal was being computed on
mangled captions, with the words "characters truncated" becoming tokens
shared by every affected pair.

Two deliberate choices:

  * **SQL arrives in a file, not on the command line.** No shell quoting, so
    a query containing quotes, newlines or `$` cannot be mangled or
    misinterpreted on the way in.
  * **The result leaves as JSON in a file**, fetched over SFTP. Nothing
    round-trips through a terminal, so output size and encoding stop
    mattering.

    .venv/bin/python jsonq.py .query.sql .result.json
"""
import datetime
import decimal
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/home/rastlyn/virallab")
from sqlalchemy import create_engine, text  # noqa: E402


def encode(o):
    if isinstance(o, (datetime.datetime, datetime.date, datetime.time)):
        return o.isoformat()
    if isinstance(o, decimal.Decimal):
        return float(o)
    if isinstance(o, (bytes, memoryview)):
        return bytes(o).hex()
    return str(o)


def main() -> int:
    sql_path, out_path = Path(sys.argv[1]), Path(sys.argv[2])
    stmt = sql_path.read_text(encoding="utf-8")
    eng = create_engine(os.environ["VIRALLAB_DB_URL"])
    with eng.begin() as c:
        r = c.execute(text(stmt))
        if r.returns_rows:
            cols = list(r.keys())
            rows = [dict(zip(cols, row)) for row in r.all()]
            payload = {"columns": cols, "rows": rows, "rowcount": len(rows)}
        else:
            payload = {"columns": [], "rows": [], "rowcount": r.rowcount}
    out_path.write_text(json.dumps(payload, default=encode),
                        encoding="utf-8")
    print(f"rows={payload['rowcount']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
