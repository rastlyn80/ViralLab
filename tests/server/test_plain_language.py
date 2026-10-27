"""The language layer must not be able to say something untrue.

A plain-English surface over a rigorous analysis is the most dangerous
part of this product, because a sentence is believed and a coefficient
is skipped. Every test here is an attempt to make the renderer state
something the evidence does not support, and an assertion that it
cannot.

Four specific claims are guarded, because each one has already been
made somewhere in this project by accident or was one edit away:

* that `text_region_score` measures text;
* that a correlation shows an effect;
* that the Instagram skip rate is a clean organic measurement;
* that a finding on one platform says anything about the other.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from server.db.models import Base, FeatureEvidence
from server.features import catalogue as cat
from server.features import lexicon as lex
from server.features import visual_structure as vs
from server.research import insights as ins
from tests.conftest import TEST_DB_URL, assert_disposable

DB_URL = TEST_DB_URL
pytestmark = pytest.mark.skipif(
    not DB_URL, reason="VIRALLAB_TEST_DB_URL not set")
if DB_URL:
    assert_disposable(DB_URL)

#: Verbs and phrasings that assert a cause. None may appear in any
#: generated sentence. "improves" is the one a language model reaches
#: for unprompted, which is exactly why it is listed.
CAUSAL = (
    " causes", " caused", " improves", " improved by", " boosts",
    " increases your", " will increase", " will improve", " leads to",
    " drives ", " guarantees", " makes your", " because of this",
    "if you add", "adding more", "you should add",
)

#: Phrasings that turn a rank correlation into an effect size.
PERCENTAGE_CLAIMS = ("% more", "% fewer", "% better", "% improvement",
                     "percent more", "percent fewer")


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


def _covered(session, feature: str, mv: str, n: int = 3) -> None:
    """Give a feature stored values, so the catalogue grades it at all.

    Without this the feature is correctly Unavailable - no extractor
    output means nothing to say - and the insight layer skips it. The
    tests below are about what the language says when a finding DOES
    exist, so the coverage has to exist too.
    """
    from server.db.models import Creator, Video, VideoFeature

    c = session.query(Creator).filter_by(handle="owner").one_or_none()
    if c is None:
        c = Creator(platform="instagram", handle="owner",
                    is_own_account=True, source="own_api")
        session.add(c)
        session.flush()
    for i in range(n):
        v = Video(creator_id=c.id, platform="instagram",
                  external_id=f"{feature}-{i}", source="own_api")
        session.add(v)
        session.flush()
        session.add(VideoFeature(
            video_id=v.id, feature=feature, method_version=mv,
            method="deterministic", value_num=1.0, status="ok"))
    session.flush()


def _evidence(session, feature: str, mv: str, **kw) -> FeatureEvidence:
    row = FeatureEvidence(
        feature=feature, method_version=mv,
        outcome=kw.pop("outcome", "reels_skip_rate"),
        population=kw.pop("population", "own_instagram_retention"),
        analysis=kw.pop("analysis", "part5_visual_structure"),
        n=kw.pop("n", 229),
        controls=kw.pop("controls", list(vs.AD_CONTROLS)),
        **kw)
    session.add(row)
    session.flush()
    return row


def _all_prose(i: ins.Insight) -> str:
    return " ".join([
        i.headline, i.explanation, i.why_it_may_matter,
        i.suggested_experiment or "", " ".join(i.limitations)]).lower()


# ---------------------------------------------------------------------------
# text_region_score is not text
# ---------------------------------------------------------------------------

def test_the_text_proxy_is_never_described_as_text():
    """It is a measure of visual busyness where captions usually sit.
    Calling it "amount of text" would report a finding nobody made -
    the thing that actually reads text is `ocr_word_count`.
    """
    p = lex.PLAIN["text_region_score"]
    assert "text" in p.not_a_measure_of.lower()
    blurb = (p.display_name + " " + p.measures).lower()
    for claim in ("amount of text", "how much text", "number of words",
                  "text quantity", "words on screen"):
        assert claim not in blurb, claim
    # ...and the field that DOES read text says so plainly.
    reads_text = lex.PLAIN["ocr_word_count"].display_name.lower()
    assert "words on screen" in reads_text


def test_an_insight_about_the_text_proxy_carries_the_disclaimer(session):
    _covered(session, "text_region_score", cat.HOOK)
    _evidence(session, "text_region_score", cat.HOOK,
              q_value=0.0, p_value=0.0, rho=-0.292, partial_rho=-0.248)
    found = [i for i in ins.build(session)
             if i.feature == "text_region_score"]
    assert found, "the fixture should produce one insight"
    prose = _all_prose(found[0])
    assert "not a measure of" in prose
    assert "does not read, count or detect words" in prose


# ---------------------------------------------------------------------------
# correlation is not cause, and is not a percentage
# ---------------------------------------------------------------------------

def test_no_generated_sentence_claims_a_cause(session):
    for feature in ("edge_density", "background_edge_density",
                    "saturation_mean"):
        _covered(session, feature, cat.HOOK)
        _evidence(session, feature, cat.HOOK, q_value=0.001, p_value=0.0,
                  rho=-0.4 if feature != "saturation_mean" else 0.25)
    built = ins.build(session)
    assert built
    for i in built:
        prose = _all_prose(i)
        for bad in CAUSAL:
            assert bad not in prose, f"{i.feature}: causal phrasing {bad!r}"
        assert "association, not proof" in prose


def test_no_correlation_is_rendered_as_a_percentage(session):
    _covered(session, "edge_density", cat.HOOK)
    _evidence(session, "edge_density", cat.HOOK, q_value=0.001,
              p_value=0.0, rho=-0.389)
    for i in ins.build(session):
        prose = _all_prose(i)
        for bad in PERCENTAGE_CLAIMS:
            assert bad not in prose, bad
    # The magnitude scale is verbal, and deliberately coarse.
    assert ins.magnitude(-0.389) == "a moderate tendency"
    assert ins.magnitude(-0.9) == "a strong tendency"
    assert ins.magnitude(0.01) == "barely any tendency"
    assert ins.magnitude(None) == "an unmeasured tendency"


def test_direction_comes_from_the_outcome_not_the_sign(session):
    """Skip rate is good when it falls. A layer reading "negative means
    bad" would invert every finding in the product."""
    assert lex.better_when("reels_skip_rate") == "lower"
    _covered(session, "edge_density", cat.HOOK)
    _evidence(session, "edge_density", cat.HOOK, q_value=0.001,
              p_value=0.0, rho=-0.389)
    _covered(session, "saturation_mean", cat.HOOK)
    _evidence(session, "saturation_mean", cat.HOOK, q_value=0.001,
              p_value=0.0, rho=0.247)
    by = {i.feature: i for i in ins.build(session)}
    assert by["edge_density"].direction == "encouraging"
    assert by["saturation_mean"].direction == "discouraging"
    assert "fewer viewers skipping" in by["edge_density"].headline
    assert "more viewers skipping" in by["saturation_mean"].headline


def test_an_outcome_with_no_known_direction_gets_no_interpretation():
    assert lex.better_when("something_nobody_defined") == "unknown"
    assert ins._good_or_bad(-0.5, "unknown") == ""


# ---------------------------------------------------------------------------
# withheld and unavailable may never read as findings
# ---------------------------------------------------------------------------

def test_a_withheld_measurement_never_becomes_an_insight(session):
    """Even with evidence that would otherwise qualify. This is the
    single most important test in the file: the whole point of the
    withheld list is that it survives contact with a good-looking
    correlation."""
    assert "vision_piano_or_keyboard_visible" in vs.WITHHELD_VISION
    _covered(session, "vision_piano_or_keyboard_visible", cat.VISION)
    _evidence(session, "vision_piano_or_keyboard_visible", cat.VISION,
              q_value=0.0, p_value=0.0, rho=-0.7, n=258)
    built = ins.build(session)
    assert not any(i.feature == "vision_piano_or_keyboard_visible"
                   for i in built)


def test_a_withheld_measurement_appears_as_a_gap_with_its_reason(session):
    gaps = {g["feature"]: g for g in ins.gaps(session)}
    g = gaps["vision_piano_or_keyboard_visible"]
    assert g["why_unavailable"]
    assert "withheld" in g["why_unavailable"].lower()
    # ...and in language a reader can use.
    assert g["display_name"] == "Piano keys visible"


def test_a_feature_with_no_extractor_is_a_gap_not_a_silence(session):
    gaps = {g["feature"] for g in ins.gaps(session)}
    assert "audio_tempo_bpm" in gaps
    assert "repertoire_work" in gaps


def test_an_uncorrected_row_is_not_labelled_associated(session):
    """Row-level grading. A feature whose OTHER finding was corrected
    must not lend that grade to this one - which it did, until a real
    report showed an Arm A row wearing an Arm B grade.
    """
    _covered(session, "edge_density", cat.HOOK)
    _evidence(session, "edge_density", cat.HOOK, q_value=0.001,
              p_value=0.0, rho=-0.389)
    weak = _evidence(session, "edge_density", cat.HOOK, q_value=0.90,
                     p_value=0.4, rho=-0.05, outcome="reels_skip_rate",
                     population="own_instagram_retention",
                     analysis="some_other_analysis")
    assert ins.row_status(weak) == "observed"
    strong = [r for r in session.query(FeatureEvidence).all()
              if r.analysis == "part5_visual_structure"][0]
    assert ins.row_status(strong) == "associated"


def test_a_row_without_the_ad_covariate_is_not_associated(session):
    row = _evidence(session, "edge_density", cat.HOOK, q_value=0.001,
                    p_value=0.0, rho=-0.389,
                    controls=["duration_s", "age_days"])
    assert ins.row_status(row) == "hypothesised"


# ---------------------------------------------------------------------------
# the platform boundary
# ---------------------------------------------------------------------------

def test_every_insight_names_its_platform_and_refuses_to_generalise(
        session):
    _covered(session, "edge_density", cat.HOOK)
    _evidence(session, "edge_density", cat.HOOK, q_value=0.001,
              p_value=0.0, rho=-0.389)
    for i in ins.build(session):
        assert i.platform == "instagram"
        joined = " ".join(i.limitations).lower()
        assert "instagram only" in joined
        assert "does not carry over" in joined or "carries over" in joined


def test_a_population_with_no_known_platform_is_withheld(session):
    """Rather than shown without one. "Which platform is this about"
    is part of the finding, not decoration around it."""
    _evidence(session, "edge_density", cat.HOOK, q_value=0.001,
              p_value=0.0, rho=-0.389, population="somewhere_unmapped")
    assert ins.build(session, population="somewhere_unmapped") == []


def test_the_skip_rate_carries_its_measurement_caveat():
    """ADR 0008: Meta never states whether the skip-rate denominator
    is the organic view count, so it may include paid arrivals."""
    o = lex.OUTCOMES["reels_skip_rate"]
    low = (o.limitations + " " + o.not_a_measure_of).lower()
    assert "adr 0008" in low or "nobody outside meta" in low
    assert "watch time" in low
    assert o.better_when == "lower"


def test_a_skip_rate_insight_repeats_that_caveat_where_it_is_read(session):
    _covered(session, "edge_density", cat.HOOK)
    _evidence(session, "edge_density", cat.HOOK, q_value=0.001,
              p_value=0.0, rho=-0.389)
    i = ins.build(session)[0]
    assert "organic" in i.explanation.lower()


# ---------------------------------------------------------------------------
# the lexicon itself
# ---------------------------------------------------------------------------

def test_no_reader_ever_meets_a_column_name():
    """A display name that is just the key with underscores removed is
    still better than the key, but a promoted or associated feature
    deserves a written one."""
    must_be_written = set(vs.VISION_FEATURES) | {
        "edge_density", "background_edge_density", "subject_edge_density",
        "background_grey_entropy", "background_subject_edge_ratio",
        "contrast_rms", "brightness_mean", "saturation_mean",
        "text_region_score"}
    missing = sorted(n for n in must_be_written
                     if not lex.as_dict(n)["written"])
    assert not missing, f"no plain-English record for: {missing}"


def test_a_display_name_is_never_the_raw_key():
    for name in {s.name for s in cat.SPEC}:
        assert lex.display_name(name) != name, name


def test_every_written_entry_says_what_it_measures():
    for name, p in lex.PLAIN.items():
        assert p.display_name and p.measures, name
        assert "_" not in p.display_name, name


# ---------------------------------------------------------------------------
# the glossary has to actually cover everything
# ---------------------------------------------------------------------------

def test_every_feature_in_the_registry_has_plain_english():
    """The usability bar, as an assertion.

    A glossary that covers a fifth of the measurements is not a
    glossary - the reader hits a gap and is back to guessing from the
    column name. This was 17 of 79 when the owner first tried the
    product and said nothing was usable; it is the whole list now and
    a new feature cannot be added without one.
    """
    missing = sorted(s.name for s in cat.SPEC
                     if not lex.as_dict(s.name)["written"])
    assert not missing, (
        f"{len(missing)} measurement(s) with no plain-English record: "
        f"{missing}")


def test_every_outcome_has_plain_english_and_a_direction():
    for name, o in lex.OUTCOMES.items():
        assert o.display_name and o.measures, name
        assert o.better_when in ("higher", "lower"), name


def test_a_plain_record_never_just_restates_the_key():
    """"Background edge density measures background edge density" is a
    dictionary entry that helps nobody."""
    for name, p in lex.PLAIN.items():
        words = set(name.replace("_", " ").split())
        measures = set(p.measures.lower().split())
        assert not words.issubset(measures) or len(p.measures) > 60, name


def test_the_unavailable_placeholders_say_they_measure_nothing():
    """These exist so a question returns "we cannot tell you" instead
    of a missing value something reads as "no". The wording has to make
    that unmistakable."""
    for name in ("performer_visible", "piano_visible", "hands_visible",
                 "face_visible", "framing_class", "emotional_presentation",
                 "on_screen_text_content"):
        p = lex.PLAIN[name]
        assert "not measured here" in p.display_name.lower(), name
        assert "cannot say" in p.measures.lower(), name
