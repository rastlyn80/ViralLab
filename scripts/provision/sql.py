"""Run one SQL statement against the server DB, committing. Test scaffolding.

    python sql.py "update analysis_batches set not_before=null"
    python sql.py --query "select count(*) from analysis_results"
"""
import os, sys
sys.path.insert(0, "/home/rastlyn/virallab")
from sqlalchemy import create_engine, text

args = sys.argv[1:]
query = args and args[0] == "--query"
stmt = args[1] if query else args[0]
e = create_engine(os.environ["VIRALLAB_DB_URL"])
with e.begin() as c:
    r = c.execute(text(stmt))
    if query:
        print(r.all())
    else:
        print(f"rowcount={r.rowcount}")
