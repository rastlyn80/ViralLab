"""Add audio envelopes to the Instagram fingerprint set. Runs ON Ubuntu.

The IG entries are keyed by external_id and hold no path, so the media file
is derived from the key: media/ig_<external_id>.mp4
"""
import json, sys
from pathlib import Path
sys.path.insert(0, "/home/rastlyn/virallab/probes/local_library")
from fingerprint import audio_envelope

ROOT = Path("/home/rastlyn/virallab")
fp = ROOT / "ig_fingerprints.json"
d = json.loads(fp.read_text(encoding="utf-8"))
todo = [k for k, v in d.items() if not v.get("audio")]
print(f"  {len(d)} entries, {len(todo)} need audio")
missing = 0
for i, k in enumerate(todo, 1):
    p = ROOT / "media" / f"ig_{k}.mp4"
    if not p.is_file():
        missing += 1
        continue
    d[k]["audio"] = audio_envelope(p)
    if i % 40 == 0:
        print(f"    ... {i}/{len(todo)}")
        fp.write_text(json.dumps(d), encoding="utf-8")
fp.write_text(json.dumps(d), encoding="utf-8")
got = sum(1 for v in d.values() if v.get("audio"))
print(f"  audio present on {got}/{len(d)} (missing files: {missing})")
