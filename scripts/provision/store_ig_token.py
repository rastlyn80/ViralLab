"""Store the Instagram Page token on the authoritative server. Runs ON Ubuntu."""
import json, os, sys
from pathlib import Path
sys.path.insert(0, "/home/rastlyn/virallab")
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from server.collectors.tokens import TokenStore
from server.collectors.base import upsert_creator

P = Path("/home/rastlyn/virallab/.ig_token.json")
d = json.loads(P.read_text(encoding="utf-8"))
eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
S = sessionmaker(eng, expire_on_commit=False, future=True)
with S() as s:
    store = TokenStore(s)
    # expires_in omitted: Page tokens do not expire (ADR 0005).
    store.upsert(platform="instagram", account_ref=d["account_ref"],
                 access_token=d["access_token"], expires_in=None,
                 scope=d.get("scope"), page_id=d.get("page_id"))
    upsert_creator(s, platform="instagram",
                   handle=d.get("username") or d["account_ref"],
                   external_id=d["account_ref"], is_own_account=True)
    s.commit()
    row = store.get("instagram", d["account_ref"])
    print(f"  stored on server: token id {row.id}, expires_at={row.expires_at} "
          f"(None = does not expire), needs_reauth={row.needs_reauth}")
