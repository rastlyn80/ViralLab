"""Cross-check the automatic master links against an INDEPENDENT signal.

The matcher decides from frame hashes and audio. It never reads the local
filename or the publication caption. So asking whether
`Chopin Concerto.mp4` is linked to a post whose caption mentions Chopin is a
genuinely independent test - it cannot agree with the matcher by
construction, only by both being right about the same video.

This is **not** ground truth. It is a second, weaker, independent witness:

  * many filenames are dates or neutral ("0630.mp4", "untitled.mp4") and
    carry no content words at all - those are *uninformative*, not wrong;
  * a caption may describe the feeling rather than the repertoire;
  * agreement on a common word ("piano") means little, so common words are
    discounted by how often they appear across the corpus.

What it CAN do is find the case worth worrying about: a link whose filename
and caption name **different composers or different works**. That is a
contradiction between independent signals and a real reason to look.

    python scripts/validate_links_independently.py
    python scripts/validate_links_independently.py --sample 15
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dbq import query  # noqa: E402
from remote import connect  # noqa: E402

LINKS_SQL = """
select l.id, l.status, l.score, l.audio_similarity, l.audio_method,
       l.decided_by, l.runner_up, l.evidence,
       m.filename, m.quick_id,
       v.external_id, v.caption, v.posted_at::date::text as posted,
       p.title as performance
from video_master_links l
join local_masters m on m.id = l.master_id
join videos v on v.id = l.video_id
left join performances p on p.id = m.performance_id
order by l.score desc
"""

#: Transliteration and spelling variants of one name. Without this the
#: checker cried wolf: `Rachmaninov Prelude.mp4` against a caption saying
#: "Rachmaninoff" was reported as naming a different composer, which is a
#: defect in the checker rather than a problem with the link.
ALIASES = {
    "rachmaninov": "rachmaninoff",
    "prokofieff": "prokofiev",
    "scriabine": "scriabin",
    "debussey": "debussy",
    "tchaikowsky": "tchaikovsky",
    "chopins": "chopin",
    "beethovens": "beethoven",
    "bachs": "bach",
    "improvisation": "improv",
    "improvisations": "improv",
    "valse": "waltz",
}

#: Words that identify content. Composers and work names are the useful
#: ones; the rest of a caption is usually mood or call-to-action.
COMPOSERS = {
    "chopin", "beethoven", "bach", "rachmaninoff", "rachmaninov", "liszt",
    "debussy", "ravel", "mozart", "schumann", "schubert", "brahms",
    "tchaikovsky", "janacek", "martinu", "satie", "grieg", "sibelius",
    "prokofiev", "scriabin", "mendelssohn", "handel", "vivaldi", "wagner",
    "faure", "albeniz", "granados", "scarlatti", "haydn", "dvorak",
}
WORKS = {
    "ballade", "nocturne", "etude", "prelude", "fugue", "sonata", "concerto",
    "waltz", "valse", "polonaise", "mazurka", "scherzo", "rhapsody",
    "impromptu", "berceuse", "barcarolle", "toccata", "partita", "suite",
    "variations", "carnaval", "liebestod", "moonlight", "pathetique",
    "winterwind", "appassionata", "improv", "improvisation",
}
STOP = {
    "the", "and", "for", "with", "that", "this", "from", "have", "what",
    "when", "your", "just", "about", "they", "were", "been", "more", "than",
    "into", "over", "also", "some", "most", "very", "like", "piano",
    "music", "video", "final", "improved", "test", "copy", "export",
    "tiktok", "reel", "reels", "insta", "instagram", "manychat", "intro",
    "hook", "raw", "colour", "color", "short", "longer", "full",
}


def words(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z]{4,}", (text or "").lower()):
        w = ALIASES.get(w, w)
        if w not in STOP:
            out.add(w)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=12)
    ap.add_argument("--out", type=Path,
                    default=Path("probes/local_library/independent_check.json"))
    args = ap.parse_args()

    c = connect()
    try:
        rows = query(c, LINKS_SQL)
    finally:
        c.close()
    if not rows:
        print("  no links in the database")
        return 1

    # Inverse document frequency over captions, so agreement on a word that
    # appears everywhere counts for little.
    caption_words = [words(r["caption"]) for r in rows]
    df = Counter(w for ws in caption_words for w in ws)
    n_docs = max(1, len(rows))

    def weight(w: str) -> float:
        base = math.log(n_docs / (1 + df.get(w, 0)))
        if w in COMPOSERS:
            base += 2.0          # a composer name is a strong identifier
        elif w in WORKS:
            base += 1.0
        return max(0.0, base)

    results = []
    for r, cws in zip(rows, caption_words):
        fws = words(Path(r["filename"]).stem)
        shared = fws & cws
        fcomp = fws & COMPOSERS
        ccomp = cws & COMPOSERS
        fwork = fws & WORKS
        cwork = cws & WORKS

        if not fws:
            verdict = "uninformative_filename"
        elif shared:
            verdict = "agrees"
        elif fcomp and ccomp and not (fcomp & ccomp):
            # Both name a composer, and they disagree. The one case worth
            # investigating.
            verdict = "contradicts_composer"
        elif fwork and cwork and not (fwork & cwork):
            verdict = "contradicts_work"
        elif not cws:
            verdict = "uninformative_caption"
        else:
            verdict = "no_overlap"

        results.append({
            "link_id": r["id"], "status": r["status"], "score": r["score"],
            "audio_similarity": r["audio_similarity"],
            "audio_method": r["audio_method"], "decided_by": r["decided_by"],
            "runner_up": r["runner_up"],
            "filename": r["filename"], "external_id": r["external_id"],
            "posted": r["posted"], "performance": r["performance"],
            "verdict": verdict,
            "shared_words": sorted(shared),
            "evidence_weight": round(sum(weight(w) for w in shared), 2),
            "filename_composers": sorted(fcomp),
            "caption_composers": sorted(ccomp),
            "filename_works": sorted(fwork),
            "caption_works": sorted(cwork),
            "variant_ambiguous": bool((r["evidence"] or {}).get(
                "variant_ambiguous")),
        })

    auto = [x for x in results if x["status"] == "auto"]
    counts = Counter(x["verdict"] for x in auto)

    print(f"  {len(results)} link(s); {len(auto)} automatic\n")
    print("=== INDEPENDENT CROSS-CHECK OF THE AUTOMATIC LINKS ===")
    print("  filename words vs caption words. The matcher reads neither, so")
    print("  this cannot agree with it by construction.\n")
    labels = {
        "agrees": "agree on a content word",
        "no_overlap": "no shared word (weak, not evidence against)",
        "uninformative_filename": "filename carries no content words",
        "uninformative_caption": "caption carries no content words",
        "contradicts_composer": "NAME DIFFERENT COMPOSERS  <- investigate",
        "contradicts_work": "name different works  <- investigate",
    }
    for key in ("agrees", "contradicts_composer", "contradicts_work",
                "no_overlap", "uninformative_filename",
                "uninformative_caption"):
        if counts[key]:
            print(f"  {counts[key]:>4}  {labels[key]}")

    informative = counts["agrees"] + counts["contradicts_composer"] \
        + counts["contradicts_work"]
    if informative:
        print(f"\n  Of the {informative} automatic links where BOTH sides "
              f"carry content words,")
        print(f"  {counts['agrees']} agree and "
              f"{counts['contradicts_composer'] + counts['contradicts_work']} "
              f"contradict.")
        print(f"  [SECONDARY] An independent-witness agreement rate, not a "
              f"measured precision.\n  Filenames and captions are both "
              f"written by the owner and can be wrong.")

    bad = [x for x in auto if x["verdict"].startswith("contradicts")]
    if bad:
        print(f"\n=== {len(bad)} CONTRADICTION(S) ===")
        for x in sorted(bad, key=lambda y: -(y["score"] or 0)):
            print(f"  link {x['link_id']}  score {x['score']:.2f}  "
                  f"audio {x['audio_similarity'] or 0:.3f}")
            print(f"    file    : {x['filename']}")
            print(f"       names : composers={x['filename_composers'] or '-'} "
                  f"works={x['filename_works'] or '-'}")
            print(f"    caption   ({x['posted']})")
            print(f"       names : composers={x['caption_composers'] or '-'} "
                  f"works={x['caption_works'] or '-'}")
    else:
        print("\n  No automatic link names a different composer or work on "
              "its two sides.")

    # Strongest independent confirmations, as a human spot-check shortlist.
    strong = sorted((x for x in auto if x["verdict"] == "agrees"),
                    key=lambda y: -y["evidence_weight"])[:args.sample]
    if strong:
        print(f"\n=== {len(strong)} STRONGEST INDEPENDENT CONFIRMATIONS ===")
        print(f"  {'weight':<8} {'score':<7} {'audio':<7} shared words")
        for x in strong:
            print(f"  {x['evidence_weight']:<8.2f} {x['score']:<7.2f} "
                  f"{x['audio_similarity'] or 0:<7.3f} "
                  f"{', '.join(x['shared_words'])[:46]}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"\n  -> {args.out}")
    print("\n  NOTE: nothing here is human verification. It is a second "
          "independent signal.\n  Human verification happens in the review "
          "UI and sets decided_by='human'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
