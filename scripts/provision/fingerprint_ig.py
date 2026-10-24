"""Fingerprint preserved Instagram media. Runs ON Ubuntu."""
import json, os, sys
from pathlib import Path
sys.path.insert(0, "/home/rastlyn/virallab")
sys.path.insert(0, "/home/rastlyn/virallab/probes/local_library")
from fingerprint import fingerprint
from sqlalchemy import create_engine, text

eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
out_path = Path("/home/rastlyn/virallab/ig_fingerprints.json")
out = json.loads(out_path.read_text()) if out_path.exists() else {}
work = Path("/home/rastlyn/virallab/_frames")

with eng.connect() as c:
    rows = c.execute(text(
        "select external_id, media_path from videos "
        "where platform='instagram' and media_status='available' "
        "order by external_id")).all()
print(f"  {len(rows)} preserved files, {len(out)} already fingerprinted")
for i, (ext, path) in enumerate(rows, 1):
    if ext in out or not path or not Path(path).is_file():
        continue
    fp = fingerprint(Path(path), work)
    if fp:
        out[ext] = fp
    if i % 40 == 0:
        print(f"    ... {i}/{len(rows)}")
        out_path.write_text(json.dumps(out))
out_path.write_text(json.dumps(out))
print(f"  fingerprinted {len(out)} -> {out_path}")
