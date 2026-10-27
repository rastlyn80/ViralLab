"""The feature catalogue, served.

M4's acceptance criterion: `GET /api/features/catalogue` returns every
feature with its grade, and the interface renders Unavailable as
Unavailable.

Read-only. The catalogue is derived from `video_features` coverage,
`visual_structure.WITHHELD_VISION` and the `feature_evidence` table on
every request rather than cached, because the thing it exists to
prevent is a stale grade: a field promoted or withheld yesterday must
not still be readable as what it was. Three grouped queries make that
cheap enough to mean it.

Nothing here writes, so there is no CSRF guard and no write dependency
- only `require_ui`, the same read credential the rest of the
interface uses.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from server.api.deps import get_session
from server.api.ui import require_ui
from server.features import catalogue as cat

logger = logging.getLogger("virallab.api.features")

router = APIRouter(prefix="/api/features", tags=["features"])


@router.get("/catalogue", dependencies=[Depends(require_ui)])
def catalogue(
    modality: str = Query("", description="filter to one modality"),
    grade: str = Query("", description="filter to one grade"),
    population: str = Query(
        "", description="restrict the evidence to one named population"),
    session: Session = Depends(get_session),
) -> dict:
    """Every feature, its grade, and why it has that grade."""
    if modality and modality not in cat.MODALITIES:
        raise HTTPException(
            422, f"unknown modality {modality!r}; "
                 f"expected one of {list(cat.MODALITIES)}")
    if grade and grade not in cat.GRADE_ORDER:
        raise HTTPException(
            422, f"unknown grade {grade!r}; "
                 f"expected one of {list(cat.GRADE_ORDER)}")

    entries = cat.build(session, population=population or None)
    # The summary is computed over everything, then the rows are
    # filtered. A filtered summary would make "3 of 79 features are
    # Associated" read as "3 of 3", which is the opposite of what a
    # reader checking coverage needs.
    full_summary = cat.summary(entries)

    shown = [e for e in entries
             if (not modality or e.spec.modality == modality)
             and (not grade or e.grade == grade)]

    return {
        "grades": list(cat.GRADE_ORDER),
        "modalities": list(cat.MODALITIES),
        "summary": full_summary,
        "filtered_to": {"modality": modality or None,
                        "grade": grade or None,
                        "population": population or None},
        "showing": len(shown),
        "features": [e.as_dict() for e in shown],
        "grade_meanings": {
            "Unavailable": ("The data or a reliable measure does not "
                            "exist. May never drive a recommendation, "
                            "and the gap is shown."),
            "Observed": ("A direct measurement on specific "
                         "publications. May be used as a description."),
            "Hypothesised": ("Suggested by the data but not tested, or "
                             "n too small. May only become an "
                             "experiment proposal."),
            "Associated": ("A correlation that survived correction and "
                           "the ad covariate, with n, rho and CI. May "
                           "be used as a hypothesis to test."),
            "Validated": ("Survived a pre-registered own-account "
                          "experiment. May drive a recommendation."),
        },
    }
