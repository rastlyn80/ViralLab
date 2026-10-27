"""M4: the registry, and the fact that a grade cannot be asserted.

The catalogue is the list of features anything downstream is allowed to
use, so the failure mode it exists to prevent is a field acquiring a
grade it did not earn. Every test here is therefore about the *ladder*
rather than about any particular feature: given this evidence, what
grade comes out, and can it be skipped.

The drift tests matter as much. A feature name that appears in
`video_features` and not in `SPEC` is invisible to every consumer that
reads the registry, and a `SPEC` entry claiming an extractor that
produces nothing is a promise the catalogue is making on behalf of code
that does not exist.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from server.db.models import (Base, Creator, FeatureEvidence, Video,
                              VideoFeature)
from server.features import catalogue as cat
from server.features import visual_structure as vs
from tests.conftest import TEST_DB_URL, assert_disposable

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)

DB_URL = TEST_DB_URL
pytestmark = pytest.mark.skipif(
    not DB_URL, reason="VIRALLAB_TEST_DB_URL not set")
if DB_URL:
    assert_disposable(DB_URL)


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(DB_URL, future=True)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    S = sessionmaker(engine, expire_on_commit=False, future=True)
    with S() as s:
        s.execute(text("truncate feature_evidence, video_features, videos, "
                       "creators restart identity cascade"))
        s.commit()
        yield s
        s.rollback()


def _video(session, n: int = 1) -> list[Video]:
    c = Creator(platform="instagram", handle="owner", is_own_account=True,
                source="own_api")
    session.add(c)
    session.flush()
    out = []
    for i in range(n):
        v = Video(creator_id=c.id, platform="instagram",
                  external_id=f"v{i}", source="own_api")
        session.add(v)
        out.append(v)
    session.flush()
    return out


def _feature(session, video_id: int, name: str, mv: str) -> None:
    session.add(VideoFeature(video_id=video_id, feature=name,
                             method_version=mv, method="deterministic",
                             value_num=1.0, status="ok"))
    session.flush()


def _evidence(session, name: str, mv: str, **kw) -> FeatureEvidence:
    row = FeatureEvidence(
        feature=name, method_version=mv,
        outcome=kw.pop("outcome", "reels_skip_rate"),
        population=kw.pop("population", "p"),
        analysis=kw.pop("analysis", "part5_visual_structure"),
        n=kw.pop("n", 250),
        controls=kw.pop("controls", list(vs.AD_CONTROLS)),
        **kw)
    session.add(row)
    session.flush()
    return row


def _spec(name: str) -> cat.FeatureSpec:
    return next(s for s in cat.SPEC if s.name == name)


# ---------------------------------------------------------------------------
# the ladder, exercised without a database
# ---------------------------------------------------------------------------

def test_a_feature_with_no_extractor_is_unavailable_and_says_why():
    spec = _spec("audio_tempo_bpm")
    grade, why = cat.grade_for(spec, coverage=0, evidence=[])
    assert grade == "Unavailable"
    assert why == spec.not_built_reason
    assert why, "an Unavailable feature with no reason is just a blank"


def test_an_extractor_with_no_rows_is_unavailable_not_observed():
    """Having built something is not the same as having measured
    anything, and the difference is the whole of M2's release rule."""
    spec = cat.FeatureSpec("x", "visual", "d", "mv-1")
    grade, why = cat.grade_for(spec, coverage=0, evidence=[])
    assert grade == "Unavailable"
    assert "no publication has a stored value" in why


def test_measured_with_no_test_is_observed():
    spec = cat.FeatureSpec("x", "visual", "d", "mv-1")
    grade, _ = cat.grade_for(spec, coverage=42, evidence=[])
    assert grade == "Observed"


def test_a_withheld_field_is_unavailable_however_well_it_scores(session):
    """BLUEPRINT.md §2: withheld measurements are Unavailable until a
    held-out benchmark promotes them. Evidence does not override that -
    a field the vision benchmark refused must not be rescued by a
    correlation.
    """
    spec = _spec("vision_sheet_music_visible")
    assert spec.withheld
    ev = [_evidence(session, spec.name, spec.method_version,
                    q_value=0.0001, p_value=0.0001, rho=0.5, n=250)]
    grade, why = cat.grade_for(spec, coverage=258, evidence=ev)
    assert grade == "Unavailable"
    assert "withheld" in why


def test_associated_needs_the_correction_the_n_and_the_ad_covariate(session):
    spec = cat.FeatureSpec("x", "visual", "d", "mv-1")

    good = _evidence(session, "x", "mv-1", q_value=0.01, p_value=0.001,
                     rho=-0.3, n=250)
    assert cat.grade_for(spec, coverage=250, evidence=[good])[0] == \
        "Associated"

    # ...and breaking any ONE requirement is enough to block it.
    for kw, missing in (
            ({"q_value": 0.20}, "q="),
            ({"n": 40}, "below"),
            ({"controls": ["duration_s", "age_days"]},
             "ad_exposure not among the controls")):
        session.execute(text("delete from feature_evidence"))
        fields = {"q_value": 0.01, "p_value": 0.001, "rho": -0.3, "n": 250}
        fields.update(kw)
        row = _evidence(session, "x", "mv-1", **fields)
        grade, why = cat.grade_for(spec, coverage=250, evidence=[row])
        assert grade != "Associated", (kw, why)
        assert missing in why, (kw, why)


def test_an_uncorrected_raw_signal_is_hypothesised_not_associated(session):
    spec = cat.FeatureSpec("x", "visual", "d", "mv-1")
    row = _evidence(session, "x", "mv-1", q_value=0.40, p_value=0.01,
                    rho=0.2, n=250)
    grade, why = cat.grade_for(spec, coverage=250, evidence=[row])
    assert grade == "Hypothesised"
    assert "did not survive" in why


def test_a_tested_feature_with_no_signal_at_all_stays_observed(session):
    spec = cat.FeatureSpec("x", "visual", "d", "mv-1")
    row = _evidence(session, "x", "mv-1", q_value=0.9, p_value=0.8,
                    rho=0.01, n=250)
    grade, why = cat.grade_for(spec, coverage=250, evidence=[row])
    assert grade == "Observed"
    assert "no correlation survived" in why


def test_nothing_can_reach_validated_without_the_experiment(session):
    """Validated requires the Phase 8 pre-registered experiment, which
    has not run. The ladder implements it so the gap stays visible."""
    spec = cat.FeatureSpec("x", "visual", "d", "mv-1")
    strong = _evidence(session, "x", "mv-1", q_value=0.0, p_value=0.0,
                       rho=0.9, n=5000)
    assert cat.grade_for(spec, coverage=5000,
                         evidence=[strong])[0] == "Associated"

    session.execute(text("delete from feature_evidence"))
    exp = _evidence(session, "x", "mv-1", q_value=0.01, p_value=0.001,
                    rho=0.4, n=200, analysis="phase8_experiment")
    assert cat.grade_for(spec, coverage=200,
                         evidence=[exp])[0] == "Validated"


def test_every_entry_carries_a_reason_for_its_grade(session):
    """A grade with no reason is an assertion wearing a measurement's
    clothes."""
    _video(session, 1)
    for e in cat.build(session):
        assert e.grade_reason, e.spec.name


# ---------------------------------------------------------------------------
# built against real rows
# ---------------------------------------------------------------------------

def test_the_catalogue_grades_from_coverage_and_evidence(session):
    videos = _video(session, 3)
    for v in videos:
        _feature(session, v.id, "edge_density", cat.HOOK)
    _evidence(session, "edge_density", cat.HOOK, q_value=0.001,
              p_value=0.0001, rho=-0.39, partial_rho=-0.33, n=247)

    entries = {e.spec.name: e for e in cat.build(session)}
    ed = entries["edge_density"]
    assert ed.coverage == 3
    assert ed.grade == "Associated"
    assert ed.evidence[0]["q_value"] == 0.001
    # ...while a sibling with an extractor and no rows is Unavailable.
    assert entries["contrast_rms"].grade == "Unavailable"


def test_population_filters_the_evidence_but_not_the_feature_list(session):
    videos = _video(session, 2)
    for v in videos:
        _feature(session, v.id, "edge_density", cat.HOOK)
    _evidence(session, "edge_density", cat.HOOK, q_value=0.001,
              p_value=0.0001, rho=-0.4, n=247, population="own_instagram")

    all_entries = {e.spec.name: e for e in cat.build(session)}
    assert all_entries["edge_density"].grade == "Associated"

    other = {e.spec.name: e
             for e in cat.build(session, population="somewhere_else")}
    assert len(other) == len(all_entries), "the feature list is fixed"
    assert other["edge_density"].grade == "Observed", (
        "evidence from another population must not grade this one")


def test_the_summary_adds_up(session):
    _video(session, 1)
    entries = cat.build(session)
    s = cat.summary(entries)
    assert s["features"] == len(entries) == len(cat.SPEC)
    assert sum(s["by_grade"].values()) == len(entries)
    assert s["usable_in_a_recommendation"] == sum(
        1 for e in entries if e.grade in ("Associated", "Validated"))


# ---------------------------------------------------------------------------
# drift, in both directions
# ---------------------------------------------------------------------------

def test_every_stored_feature_name_appears_in_the_spec(session):
    """A feature in the database and not in the registry is invisible
    to every consumer that reads the registry - which is the point of
    having one."""
    _video(session, 1)
    rows = session.execute(
        text("select distinct feature from video_features")).all()
    known = {s.name for s in cat.SPEC}
    missing = sorted({f for (f,) in rows} - known)
    assert not missing, f"stored but not in SPEC: {missing}"


def test_the_spec_has_no_duplicate_names():
    names = [s.name for s in cat.SPEC]
    assert len(names) == len(set(names))


def test_every_spec_entry_is_either_built_or_explains_why_not():
    for s in cat.SPEC:
        built = bool(s.method_version)
        assert built != bool(s.not_built_reason), (
            f"{s.name}: a feature has a method version or a reason it "
            f"has none, never both and never neither")


def test_every_spec_entry_has_a_known_modality_and_a_description():
    for s in cat.SPEC:
        assert s.modality in cat.MODALITIES, s.name
        assert len(s.describes) > 15, s.name


def test_the_withheld_flag_matches_the_vision_registry():
    """Two lists of withheld fields would drift, and the catalogue's
    copy is the one a consumer reads. So it is checked against
    `visual_structure`, which is where the benchmark writes."""
    spec_withheld = {s.name for s in cat.SPEC if s.withheld}
    # Everything the vision registry withholds and the catalogue knows
    # about must be flagged.
    known = {s.name for s in cat.SPEC}
    for name in vs.WITHHELD_VISION:
        if name in known:
            assert name in spec_withheld, (
                f"{name} is withheld by visual_structure and not flagged "
                f"in the catalogue")
    # ...and nothing may be flagged withheld that the registry permits.
    promoted = set(vs.VISION_FEATURES)
    assert not (spec_withheld & promoted), (
        f"flagged withheld but promoted: {spec_withheld & promoted}")


def test_a_promoted_vision_field_is_not_marked_withheld():
    for name in vs.VISION_FEATURES:
        spec = _spec(name)
        assert not spec.withheld, name


def test_platform_availability_is_recorded_for_every_feature():
    for s in cat.SPEC:
        assert s.platforms, s.name
        for p in s.platforms:
            assert p.platform in ("instagram", "tiktok"), s.name
            if not p.available:
                assert p.reason, (
                    f"{s.name}: unavailable on {p.platform} with no "
                    f"reason is indistinguishable from a bug")
