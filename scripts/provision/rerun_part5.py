"""Re-run Phase 6C Part 5 with the M1 ad-exposure covariate. Runs ON Ubuntu.

Emits JSON for `docs/benchmarks/6c-visual-density.md` to be amended from.

**Two things changed at once in M1** and the output keeps them apart,
because a reader given only the new figures could not say which change
moved them:

  A. `original_no_ad`  — the 48 publications that had a retention figure
     before 2026-10-26, with no ad control. Reproduces the published run.
  B. `original_with_ad` — the same 48, with the covariate. Isolates what
     the control does.
  C. `full_with_ad`    — every publication that now has a retention
     figure, with the covariate. The new best estimate.

The population grew because retention metrics had never been *requested*
for most of the back catalogue, not because the platform withheld them:
183 publications gained a skip rate on 2026-10-26, and every one of them
had exactly one older snapshot carrying no `retention_status` field at all.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict

sys.path.insert(0, "/home/rastlyn/virallab")

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from server.analytics import ad_exposure  # noqa: E402
from server.features import retention, visual_structure  # noqa: E402

#: Publications that carried a retention figure BEFORE the M1 collection
#: pass. This is the population the published Part 5 was computed on, and
#: it reproduces as 49 publications of which 48 have media — exactly the
#: n the document reports.
#:
#: The cutoff is the start of the first `surface_backfill` run rather than
#: a wall-clock date: Phase 6C itself ran on 2026-10-26, so a midnight
#: boundary would have excluded snapshots the published analysis used and
#: silently shrunk the comparison population to 28.
ORIGINAL_SQL = """
select distinct v.id
from videos v
     join creators c on c.id = v.creator_id
     join metrics_snapshots s on s.video_id = v.id
where v.platform = 'instagram' and c.is_own_account
  and s.raw ? 'reels_skip_rate'
  and s.captured_at < (select min(started_at) from collection_runs
                       where kind = 'surface_backfill')
"""


def vs_summary(vs) -> dict:
    return {
        "n_population": vs.n_population,
        "n_with_media": vs.n_with_media,
        "ad_covariate_source": vs.ad_covariate_source,
        "ad_covariate_n": vs.ad_covariate_n,
        "ad_covariate_exposed": vs.ad_covariate_exposed,
        "ad_outcome_rho": vs.ad_outcome_rho,
        "ad_outcome_p": vs.ad_outcome_p,
        "ad_covariate_note": vs.ad_covariate_note,
        "arm_a": [asdict(a) for a in vs.proxy_associations],
        "arm_b": [asdict(a) for a in vs.outcome_associations],
        "arm_c": [asdict(m) for m in vs.mediation],
        "verdict_a": vs.verdict_a,
        "verdict_b": vs.verdict_b,
    }


def main() -> int:
    eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    S = sessionmaker(eng, expire_on_commit=False, future=True)
    out: dict = {}
    with S() as s:
        original = {r[0] for r in s.execute(text(ORIGINAL_SQL)).all()}
        out["original_population_size"] = len(original)

        exposures = ad_exposure.resolve(s, platform="instagram")
        out["exposure_coverage"] = ad_exposure.coverage(exposures)
        out["ad_window"] = ad_exposure.ad_window(s)

        out["A_original_no_ad"] = vs_summary(visual_structure.analyse(
            s, population=original, control_ad_exposure=False))
        out["B_original_with_ad"] = vs_summary(visual_structure.analyse(
            s, population=original))
        out["C_full_with_ad"] = vs_summary(visual_structure.analyse(s))

        # The outcome family the owner asked for on 2026-10-26: retention
        # and per-viewer engagement rates, not views. Run on the full
        # population, which is the only one large enough to carry six
        # outcomes.
        out["outcomes"] = {}
        for name in retention.OUTCOMES:
            rep = retention.analyse(s, outcome=name)
            out["outcomes"][name] = {
                "n_analysed": rep.n_analysed,
                "ad_covariate_source": rep.ad_covariate_source,
                "ad_covariate_n": rep.ad_covariate_n,
                "ad_covariate_exposed": rep.ad_covariate_exposed,
                "ad_outcome_rho": rep.ad_outcome_rho,
                "ad_outcome_p": rep.ad_outcome_p,
                "survivors": rep.survivors,
                "verdict": rep.verdict,
                "associations": [
                    {"feature": a.feature, "n": a.n, "rho": a.rho,
                     "p": a.p_value, "q": a.p_adjusted,
                     "partial_dur": a.rho_partial_duration,
                     "partial_dur_age": a.rho_partial_duration_age,
                     "partial_dur_age_ad": a.rho_partial_duration_age_ad,
                     "n_ad": a.n_ad_controlled,
                     "caveats": a.caveats}
                    for a in rep.associations],
            }

        # §5's OCR juxtaposition: real text measurement against the
        # proxy that was never reading text. Re-runnable at the full n
        # only once OCR has been backfilled past its original 48.
        try:
            ex = retention.explain_proxy(s)
            out["ocr_juxtaposition"] = {
                "n": ex.n,
                "proxy_rho": ex.proxy_rho,
                "proxy_vs_ocr": ex.proxy_vs_ocr,
                "controlled_rho": ex.controlled_rho,
                "explained": ex.explained,
                "verdict": ex.verdict,
                "caveats": ex.caveats,
            }
        except Exception as exc:              # noqa: BLE001 - recorded
            out["ocr_juxtaposition"] = {"error": str(exc)[:300]}

    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
