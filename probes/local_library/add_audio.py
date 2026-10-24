"""Add audio envelopes to an existing fingerprint set (no frame re-extraction)."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from fingerprint import audio_envelope

fp_path = Path(sys.argv[1])
path_key = sys.argv[2] if len(sys.argv) > 2 else "path"
d = json.loads(fp_path.read_text(encoding="utf-8"))
todo = [k for k, v in d.items() if not v.get("audio")]
print(f"  {len(d)} entries, {len(todo)} need audio")
for i, k in enumerate(todo, 1):
    p = Path(d[k].get(path_key) or "")
    if not p.is_file():
        continue
    d[k]["audio"] = audio_envelope(p)
    if i % 40 == 0:
        print(f"    ... {i}/{len(todo)}")
        fp_path.write_text(json.dumps(d), encoding="utf-8")
fp_path.write_text(json.dumps(d), encoding="utf-8")
got = sum(1 for v in d.values() if v.get("audio"))
print(f"  audio present on {got}/{len(d)}")
