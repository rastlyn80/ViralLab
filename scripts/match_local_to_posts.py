"""Match local masters to published posts, and queue the uncertain ones.

Scoring combines two independent signals:

  * **frame-sequence overlap** — the fraction of sampled frames that align,
    computed at the best offset so a post using only part of a master still
    scores on the overlap. This is what supports edited variants and reposts.
  * **duration agreement** — a weak signal on its own, but it separates
    "same footage, same cut" from "same footage, different cut".

Three bands, and the middle one is the point:

  | score | action |
  |-------|--------|
  | >= 0.80 | auto-link |
  | 0.45 - 0.80 | **queued for review** — never silently accepted |
  | < 0.45 | discarded |

A master may legitimately match several posts (a repost, a re-edit), so the
matcher does not force one-to-one. A post matching several masters IS
reported, because that usually means near-duplicate masters and the owner
should see it.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / "probes" / "local_library"))
from fingerprint import best_overlap, envelope_similarity  # noqa: E402

AUTO_LINK = 0.80
REVIEW_FLOOR = 0.45
#: Durations this far apart (seconds) cannot be the same cut, though they may
#: still be an edited variant, which the frame overlap will show.
DURATION_TOLERANCE = 1.5


def load_hashes(entry: dict) -> list[int]:
    return [int(h, 16) for h in entry["hashes"]]


#: Below this, two clips are not the same recording however similar they
#: look. Calibrated on the owner's "musical portraits" series, which are
#: visually identical and scored 0.58-0.64 against each other.
AUDIO_MATCH_FLOOR = 0.80


def score(local: dict, post: dict) -> tuple[float, dict]:
    """Combine visual and audio agreement.

    Visual alone is NOT sufficient. The owner's portrait series share an
    identical setup - same framing, same piano, ~9 s each - and differ only
    in the music, so frame hashing alone produced confident FALSE matches at
    overlap 1.00. Audio is what separates them, and a visual match without
    audio support is therefore demoted rather than trusted.
    """
    lh, ph = load_hashes(local), load_hashes(post)
    overlap, offset = best_overlap(lh, ph)
    d_local = local.get("duration_s") or 0
    d_post = post.get("duration_s") or 0
    d_delta = abs(d_local - d_post)
    same_cut = d_delta <= DURATION_TOLERANCE

    audio = envelope_similarity(local.get("audio"), post.get("audio"))
    have_audio = bool(local.get("audio") and post.get("audio"))

    visual = overlap * (1.0 if same_cut else 0.85)
    if not have_audio:
        # Cannot corroborate; cap below the auto-link threshold so it is
        # reviewed rather than assumed.
        combined = min(visual, 0.75)
    elif audio >= AUDIO_MATCH_FLOOR:
        combined = 0.65 * visual + 0.35 * audio
    else:
        # Looks the same, sounds different: almost certainly a sibling in a
        # series, not the source. Keep it visible but well below auto-link.
        combined = min(visual, 0.60) * 0.8
    return combined, {
        "frame_overlap": round(overlap, 3),
        "audio_similarity": round(audio, 3),
        "has_audio": have_audio,
        "offset": offset,
        "duration_delta_s": round(d_delta, 2),
        "same_cut": same_cut,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", type=Path,
                    default=Path("probes/local_library/fingerprints.json"))
    ap.add_argument("--posts", type=Path,
                    default=Path("probes/local_library/ig_fingerprints.json"))
    ap.add_argument("--platform", default="instagram")
    ap.add_argument("--out", type=Path,
                    default=Path("probes/local_library/matches.json"))
    args = ap.parse_args()

    local = json.loads(args.local.read_text(encoding="utf-8"))
    posts = json.loads(args.posts.read_text(encoding="utf-8"))
    print(f"  {len(local)} local masters x {len(posts)} posts "
          f"= {len(local)*len(posts):,} comparisons")

    auto, review = [], []
    per_post_best: dict[str, list] = defaultdict(list)

    for pid, post in posts.items():
        best = []
        for qid, loc in local.items():
            s, detail = score(loc, post)
            if s >= REVIEW_FLOOR:
                best.append((s, qid, detail))
        best.sort(reverse=True, key=lambda x: x[0])
        per_post_best[pid] = best[:3]
        if not best:
            continue
        top_score, top_qid, detail = best[0]
        runner_up = best[1][0] if len(best) > 1 else 0.0
        row = {
            "post_id": pid, "platform": args.platform,
            "quick_id": top_qid,
            "local_path": local[top_qid]["path"],
            "local_name": local[top_qid]["name"],
            "score": round(top_score, 3),
            "runner_up": round(runner_up, 3),
            "ambiguous": runner_up >= top_score - 0.08,
            **detail,
        }
        # An ambiguous top-2 goes to review even above the auto threshold:
        # picking arbitrarily between near-equal candidates is exactly the
        # silent error this is meant to avoid.
        if top_score >= AUTO_LINK and not row["ambiguous"]:
            auto.append(row)
        else:
            row["alternatives"] = [
                {"quick_id": q, "name": local[q]["name"],
                 "score": round(sc, 3)} for sc, q, _ in best[1:3]]
            review.append(row)

    matched_posts = {r["post_id"] for r in auto} | {r["post_id"] for r in review}
    unmatched_posts = [p for p in posts if p not in matched_posts]
    used_locals = {r["quick_id"] for r in auto}
    unused_locals = [q for q in local if q not in used_locals]

    # One master legitimately mapping to several posts: a repost or re-edit.
    multi = defaultdict(list)
    for r in auto:
        multi[r["quick_id"]].append(r["post_id"])
    multi = {k: v for k, v in multi.items() if len(v) > 1}

    out = {"auto": auto, "review": review,
           "unmatched_posts": unmatched_posts,
           "unused_locals": unused_locals,
           "one_master_many_posts": multi}
    args.out.write_text(json.dumps(out, indent=1), encoding="utf-8")

    n = len(posts)
    print()
    print("=== MATCH RESULTS ===")
    print(f"  auto-linked (>= {AUTO_LINK})   : {len(auto)} "
          f"({len(auto)/n*100:.0f}% of posts)")
    print(f"  queued for review          : {len(review)}")
    print(f"  unmatched posts            : {len(unmatched_posts)}")
    print(f"  local masters never used   : {len(unused_locals)} "
          f"of {len(local)}")
    print(f"  one master -> several posts: {len(multi)} master(s)")
    if multi:
        for q, pids in list(multi.items())[:5]:
            print(f"      {local[q]['name'][:40]:<42} -> {len(pids)} posts")
    if auto:
        scores = sorted(r["score"] for r in auto)
        print(f"  auto-link score min/median : {scores[0]:.2f} / "
              f"{scores[len(scores)//2]:.2f}")
    print(f"\n  -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
