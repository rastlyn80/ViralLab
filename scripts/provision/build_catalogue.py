"""Populate `feature_evidence` and print the catalogue. Runs ON Ubuntu.

M4. Two steps that have to happen in this order:

1. Run Part 5's association analysis on the full own-account population
   with the ad-exposure covariate, and **persist** the result. Until
   now these numbers were printed and pasted into
   `docs/benchmarks/6c-visual-density.md`, so a grade derived from them
   was derived from prose.
2. Build the catalogue, which reads that table, and report what grade
   every feature came out at.

The analysis itself is unchanged — this calls the same
`visual_structure.analyse` the published Part 5 used, with the same
defaults, so the persisted rows are the published findings and not a
re-interpretation of them.

    python build_catalogue.py              # persist, then report
    python build_catalogue.py --dry-run    # report from what is already stored
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, "/home/rastlyn/virallab")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from server.features import catalogue as cat  # noqa: E402
from server.features import visual_structure  # noqa: E402

#: The named population these findings belong to. Named rather than
#: implied: `feature_evidence` keys on it, and a future run over a
#: different corpus must not silently overwrite these rows.
POPULATION = "own_instagram_retention"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="report from stored evidence; compute nothing")
    ap.add_argument("--population", default=POPULATION)
    args = ap.parse_args()

    eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    S = sessionmaker(eng, expire_on_commit=False, future=True)

    out: dict = {"population": args.population}
    with S() as s:
        if not args.dry_run:
            rep = visual_structure.analyse(s)
            written = visual_structure.persist_evidence(
                s, rep, population=args.population)
            s.commit()
            out["analysis"] = {
                "n_population": rep.n_population,
                "n_with_media": rep.n_with_media,
                "proxy": rep.proxy,
                "ad_covariate_source": rep.ad_covariate_source,
                "ad_covariate_n": rep.ad_covariate_n,
                "ad_covariate_exposed": rep.ad_covariate_exposed,
                "evidence_rows_written": written,
            }

        entries = cat.build(s)
        out["summary"] = cat.summary(entries)
        out["by_grade"] = {}
        for e in entries:
            out["by_grade"].setdefault(e.grade, []).append(e.spec.name)
        # The two that matter most to read: what may drive a
        # recommendation, and what is withheld despite being measured.
        out["usable"] = [
            {"name": e.spec.name, "grade": e.grade,
             "why": e.grade_reason}
            for e in entries if e.grade in ("Associated", "Validated")]
        out["withheld_but_measured"] = [
            e.spec.name for e in entries
            if e.spec.withheld and e.coverage > 0]
        out["no_extractor"] = [
            e.spec.name for e in entries if e.spec.not_built_reason]

    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
