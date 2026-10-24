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
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # repo root, for shared/
from shared import audiofp  # noqa: E402
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
#: Stage-1 screening floors. All three are deliberately well BELOW the
#: decision thresholds: the screen decides what to look at properly, never
#: what matches. A pair passes on any one of them.
SCREEN_FLOOR = 0.40
SCREEN_VISUAL_FLOOR = 0.40
SCREEN_AUDIO_FLOOR = 0.45

#: Floor for the spectral fingerprint, which is measured rather than guessed:
#: docs/benchmarks/2-audio-fingerprint.md puts known positives at >= 0.862
#: (worst case, a 48 kbit re-encode) and known negatives at <= 0.425 (worst
#: case, two portraits in the same series). 0.70 sits inside that gap with
#: margin on both sides.
SPECTRAL_FLOOR = 0.70


def audio_agreement(local: dict, post: dict, *, quick: bool = False) -> dict:
    """Best available audio comparison, with its provenance.

    Two descriptors may be present. The spectral fingerprint is preferred
    because it is the one that was benchmarked; the envelope is the fallback
    for records written before it existed. `method` is returned so a stored
    score can never be mistaken for better evidence than it had.
    """
    fa, fb = local.get("audiofp"), post.get("audiofp")
    if fa and fb:
        r = audiofp.compare(fa, fb, quick=quick)
        if r.get("comparable"):
            return {"similarity": r["score"], "method": "spectral",
                    "floor": SPECTRAL_FLOOR, "audio_offset_s": r["offset_s"],
                    "aligned_frames": r["aligned_frames"]}
    if local.get("audio") and post.get("audio"):
        return {"similarity": envelope_similarity(local["audio"],
                                                  post["audio"]),
                "method": "envelope", "floor": AUDIO_MATCH_FLOOR,
                "audio_offset_s": None, "aligned_frames": None}
    return {"similarity": 0.0, "method": "none", "floor": SPECTRAL_FLOOR,
            "audio_offset_s": None, "aligned_frames": None}


def score(local: dict, post: dict, *, quick: bool = False
          ) -> tuple[float, dict]:
    """Combine audio and visual agreement, with audio as the gate.

    Audio decides *whether* this is the same recording; visuals decide
    whether it is the same *edit*. That ordering is deliberate and comes
    from two measured facts:

      * The owner's portrait series is visually identical - same framing,
        same piano, ~9 s each - and differs only in the music. Frame hashing
        alone scored 1.00 and produced ~10 confident FALSE auto-links.
      * The owner cross-posts ~99% of videos, changing the opening hook and
        the on-screen text between platforms. Those edits change pixels, so a
        LOW visual overlap is expected on a genuine pair and must not be
        treated as disagreement.

    So a strong audio match with a weak visual one lands in review as a
    probable variant, rather than being discarded; and a strong visual match
    without audio support is never auto-linked.
    """
    lh, ph = load_hashes(local), load_hashes(post)
    overlap, offset = best_overlap(lh, ph)
    d_local = local.get("duration_s") or 0
    d_post = post.get("duration_s") or 0
    d_delta = abs(d_local - d_post)
    same_cut = d_delta <= DURATION_TOLERANCE

    ag = audio_agreement(local, post, quick=quick)
    audio, floor = ag["similarity"], ag["floor"]
    have_audio = ag["method"] != "none"

    visual = overlap * (1.0 if same_cut else 0.85)
    if not have_audio:
        # Cannot corroborate; cap below the auto-link threshold so it is
        # reviewed rather than assumed.
        combined = min(visual, 0.75)
    elif audio >= floor:
        combined = 0.55 * audio + 0.45 * visual
    else:
        # Looks the same, sounds different: almost certainly a sibling in a
        # series, not the source. Keep it visible but well below auto-link.
        combined = min(visual, 0.60) * 0.8
    return combined, {
        "frame_overlap": round(overlap, 3),
        "audio_similarity": round(audio, 3),
        "audio_method": ag["method"],
        "audio_quick": quick,
        "audio_offset_s": ag["audio_offset_s"],
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
    ap.add_argument("--performances", type=Path,
                    default=Path("probes/local_library/performances.json"),
                    help="output of group_performances.py; lets an ambiguous "
                         "top-2 be recognised as two variants of one "
                         "performance rather than a coin toss")
    ap.add_argument("--out", type=Path,
                    default=Path("probes/local_library/matches.json"))
    args = ap.parse_args()

    local = json.loads(args.local.read_text(encoding="utf-8"))
    posts = json.loads(args.posts.read_text(encoding="utf-8"))

    # quick_id -> performance label. Absent file is fine: the matcher then
    # behaves as it did before, treating every near-tie as ambiguous.
    perf_of: dict[str, str] = {}
    if args.performances and args.performances.exists():
        for g in json.loads(args.performances.read_text(encoding="utf-8")):
            for m in g["members"]:
                perf_of[m] = g["label"]
        print(f"  {len(set(perf_of.values()))} performance(s) known for "
              f"{len(perf_of)} master(s)")
    print(f"  {len(local)} local masters x {len(posts)} posts "
          f"= {len(local)*len(posts):,} comparisons")

    auto, review = [], []
    per_post_best: dict[str, list] = defaultdict(list)
    screened = rescored = 0

    for pid, post in posts.items():
        # Stage 1 - screen. A full-fidelity audio comparison costs
        # O(min(m,n) x (m+n)); on this corpus a few long masters against a
        # long Reel reach ~1.6 s per pair, which turns a full sweep into
        # hours. Screening on a 30 s window at a single phase costs ~1.4 ms.
        #
        # The screen is a UNION of two independent cheap signals, so a pair
        # only has to survive ONE of them. That matters: a Reel cut from the
        # middle of a long master may share nothing in its first 30 s, and
        # would be missed by an audio-only screen - but its frame sequence
        # still aligns. Recall is protected by not making either signal a
        # veto.
        candidates = []
        for qid, loc in local.items():
            s_q, d_q = score(loc, post, quick=True)
            screened += 1
            if (s_q >= SCREEN_FLOOR
                    or d_q["frame_overlap"] >= SCREEN_VISUAL_FLOOR
                    or d_q["audio_similarity"] >= SCREEN_AUDIO_FLOOR):
                candidates.append(qid)

        # Stage 2 - decide, at full length and all sub-frame phases.
        best = []
        for qid in candidates:
            loc = local[qid]
            s, detail = score(loc, post)
            rescored += 1
            if s >= REVIEW_FLOOR:
                best.append((s, qid, detail))
        best.sort(reverse=True, key=lambda x: x[0])
        per_post_best[pid] = best[:3]
        if not best:
            continue
        top_score, top_qid, detail = best[0]
        runner_up = best[1][0] if len(best) > 1 else 0.0
        near_tie = runner_up >= top_score - 0.08

        # A near-tie between two VARIANTS OF ONE PERFORMANCE is not a
        # coin toss. The owner keeps several exports of the same take -
        # "Billie Eilish - Improved" / "- with Intro" / "- Test 3", or
        # "Pop Etude 1" / "- Manychat" / "- TikTok" - so a post legitimately
        # scores ~1.0 against all of them. Treating that as ambiguous asked
        # the owner an unanswerable question and withheld a match that was
        # in fact certain.
        #
        # So the two kinds of near-tie are separated:
        #   * same performance -> the match is confident, only the export is
        #     open. Linked, with `variant_ambiguous` recorded so the choice
        #     of file is never mistaken for an established fact.
        #   * different performances -> genuinely ambiguous. Reviewed, as
        #     before.
        tied = [q for sc, q, _ in best[:3] if sc >= top_score - 0.08]
        perfs = {perf_of.get(q) for q in tied}
        same_perf = near_tie and len(perfs) == 1 and None not in perfs

        row = {
            "post_id": pid, "platform": args.platform,
            "quick_id": top_qid,
            "local_path": local[top_qid]["path"],
            "local_name": local[top_qid]["name"],
            "score": round(top_score, 3),
            "runner_up": round(runner_up, 3),
            "ambiguous": near_tie and not same_perf,
            **detail,
        }
        if near_tie:
            row["performance"] = perf_of.get(top_qid)
            row["performance_agreement"] = (
                "same_performance" if same_perf else "different_performances")
            row["variant_ambiguous"] = same_perf
            row["tied_variants"] = [local[q]["name"] for q in tied]

        # An ambiguous top-2 goes to review even above the auto threshold:
        # picking arbitrarily between candidates from DIFFERENT performances
        # is exactly the silent error this is meant to avoid.
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
    print(f"  screened {screened:,} pairs, re-scored {rescored:,} "
          f"at full fidelity ({rescored/max(screened,1)*100:.1f}%)")
    print(f"  one master -> several posts: {len(multi)} master(s)")
    if multi:
        for q, pids in list(multi.items())[:5]:
            print(f"      {local[q]['name'][:40]:<42} -> {len(pids)} posts")
    var_amb = [r for r in auto if r.get("variant_ambiguous")]
    if var_amb:
        print(f"  of those, {len(var_amb)} match a performance confidently "
              f"but cannot tell WHICH export was used:")
        for r in var_amb[:6]:
            print(f"      {r['performance'][:30]:<32} "
                  f"{len(r['tied_variants'])} variants tie")
        print("      -> the performance link is sound; the file named is one "
              "of the tied variants")
    if auto:
        scores = sorted(r["score"] for r in auto)
        print(f"  auto-link score min/median : {scores[0]:.2f} / "
              f"{scores[len(scores)//2]:.2f}")
    print(f"\n  -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
