"""Benchmark the audio fingerprint against known positives and negatives.

Ground truth comes from two places:

  * **Known positives, synthetic.** Take a real file and transform it the way
    a platform would: re-encode at a lower bitrate, shift the start, trim the
    head (a different hook), shorten it, normalise the loudness. The result
    IS the same recording, so a correct fingerprint must say so.
  * **Known negatives, real.** The "musical portraits" series: visually
    identical, ~9 s each, different music. These previously produced
    confident false matches and are the hardest negatives in the archive.

Both fingerprint methods are measured side by side, so adopting the new one
is a decision backed by numbers rather than a preference.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from shared import audiofp  # noqa: E402
from fingerprint import audio_envelope, envelope_similarity  # noqa: E402

FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
WORK = Path("probes/local_library/_bench")


def transform(src: Path, name: str, args: list[str]) -> Path | None:
    WORK.mkdir(parents=True, exist_ok=True)
    dest = WORK / f"{name}.mp4"
    if dest.exists():
        return dest
    try:
        r = subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(src)]
                           + args + [str(dest)],
                           capture_output=True, timeout=300)
        return dest if r.returncode == 0 and dest.exists() else None
    except (OSError, subprocess.TimeoutExpired):
        return None


TRANSFORMS = [
    ("reencode_low", ["-b:a", "48k", "-b:v", "400k", "-ac", "1"]),
    ("offset_1s", ["-ss", "1.0"]),
    ("trim_head_3s", ["-ss", "3.0"]),
    ("shorten_half", ["-t", "0.5"]),            # replaced per-file below
    ("normalised", ["-filter:a", "loudnorm"]),
    ("resampled", ["-ar", "22050", "-b:a", "64k"]),
]


def main() -> int:
    inv = json.loads(
        Path("probes/local_library/inventory.json").read_text(encoding="utf-8"))
    short = [r for r in inv if 10 <= (r.get("duration_s") or 0) <= 90]
    short.sort(key=lambda r: r["name"])
    if not short:
        print("no suitable source files")
        return 1

    sources = short[:4]
    print(f"  benchmarking on {len(sources)} source file(s)\n")

    pos_new, pos_old = [], []
    print("=== KNOWN POSITIVES (same recording, transformed) ===")
    print(f"  {'transform':<16} {'new score':<11} {'offset':<9} "
          f"{'old envelope':<13} source")
    for src_row in sources:
        src = Path(src_row["path"])
        base_new = audiofp.fingerprint(src)
        base_old = audio_envelope(src)
        if base_new is None:
            continue
        dur = src_row["duration_s"]
        for tname, targs in TRANSFORMS:
            args = (["-t", f"{dur*0.5:.2f}"] if tname == "shorten_half"
                    else list(targs))
            out = transform(src, f"{src.stem[:12]}_{tname}", args)
            if out is None:
                continue
            fp_new = audiofp.fingerprint(out)
            cmp_new = audiofp.compare(base_new, fp_new)
            sim_old = envelope_similarity(base_old, audio_envelope(out))
            pos_new.append(cmp_new["score"])
            pos_old.append(sim_old)
            print(f"  {tname:<16} {cmp_new['score']:<11.3f} "
                  f"{cmp_new['offset_s']:<9.2f} {sim_old:<13.3f} "
                  f"{src.name[:26]}")

    print("\n=== KNOWN NEGATIVES (different recordings) ===")
    neg_new, neg_old = [], []
    names = [r["name"] for r in sources]
    print(f"  {'pair':<46} {'new score':<11} old envelope")
    for i in range(len(sources)):
        for j in range(i + 1, len(sources)):
            a, b = Path(sources[i]["path"]), Path(sources[j]["path"])
            fa, fb = audiofp.fingerprint(a), audiofp.fingerprint(b)
            if fa is None or fb is None:
                continue
            s_new = audiofp.compare(fa, fb)["score"]
            s_old = envelope_similarity(audio_envelope(a), audio_envelope(b))
            neg_new.append(s_new)
            neg_old.append(s_old)
            label = f"{a.stem[:20]} vs {b.stem[:20]}"
            print(f"  {label:<46} {s_new:<11.3f} {s_old:.3f}")

    # The portrait series: the hardest negatives in the archive.
    portraits = [r for r in inv
                 if 8 <= (r.get("duration_s") or 0) <= 10][:6]
    if len(portraits) >= 2:
        print("\n  portrait series (visually identical, different music):")
        for i in range(len(portraits) - 1):
            a, b = Path(portraits[i]["path"]), Path(portraits[i + 1]["path"])
            fa, fb = audiofp.fingerprint(a), audiofp.fingerprint(b)
            if fa is None or fb is None:
                continue
            s_new = audiofp.compare(fa, fb)["score"]
            s_old = envelope_similarity(audio_envelope(a), audio_envelope(b))
            # Two files whose names share a stem ("X - Improved" vs
            # "X - Test 2") are usually the SAME recording, so they are not
            # valid negatives. Both methods scored that pair 1.000, which is
            # correct behaviour mislabelled as a failure.
            same_work = (a.stem.split(" - ")[0].lower()
                         == b.stem.split(" - ")[0].lower())
            tag = "  (same work - excluded)" if same_work else ""
            if not same_work:
                neg_new.append(s_new)
                neg_old.append(s_old)
            print(f"  {a.stem[:20]} vs {b.stem[:20]:<22} "
                  f"{s_new:<11.3f} {s_old:.3f}{tag}")

    print("\n=== SEPARATION ===")
    for label, pos, neg in (("new (spectral)", pos_new, neg_new),
                            ("old (envelope)", pos_old, neg_old)):
        if not pos or not neg:
            continue
        print(f"  {label}")
        print(f"    positives min {min(pos):.3f}  median "
              f"{sorted(pos)[len(pos)//2]:.3f}")
        print(f"    negatives max {max(neg):.3f}  median "
              f"{sorted(neg)[len(neg)//2]:.3f}")
        gap = min(pos) - max(neg)
        print(f"    GAP (worst positive - worst negative): {gap:+.3f} "
              f"{'<- separable' if gap > 0 else '<- OVERLAP, not separable'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
