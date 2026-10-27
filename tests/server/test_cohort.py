"""M6: comparing across creators without letting one of them speak for all.

The failure this guards against is subtle and would never raise. Pool
every competitor publication together and the creator with 32 videos
contributes four times what the creator with 8 does; the result reads
as "the cohort" and describes the busiest member. So the comparison
takes each creator's own median first and compares across those, and
the tests below check that it actually does - by building a cohort
where pooling and per-creator comparison give opposite answers.

The second guard is about what the comparison may claim. No retention
data exists for any creator but the owner, so a difference here is a
difference in what the videos *are* and never in what they achieve.
Nothing may be graded above Observed, whatever its p-value.
"""

from __future__ import annotations

import statistics

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from server.db.models import (Base, Creator, CreatorCollection,
                              CreatorCollectionMember, Video, VideoFeature)
from server.research import cohort as co
from shared import hookfeat
from tests.conftest import TEST_DB_URL, assert_disposable

DB_URL = TEST_DB_URL
pytestmark = pytest.mark.skipif(
    not DB_URL, reason="VIRALLAB_TEST_DB_URL not set")
if DB_URL:
    assert_disposable(DB_URL)

FEATURE = "edge_density"
MV = hookfeat.METHOD_VERSION


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
        s.execute(text(
            "truncate creator_collection_members, creator_collections, "
            "video_features, videos, creators restart identity cascade"))
        s.commit()
        yield s
        s.rollback()


def _creator(session, handle, *, own=False, followers=1000):
    c = Creator(platform="tiktok", handle=handle, is_own_account=own,
                source="own_api" if own else "discovery",
                follower_count=None if own else followers)
    session.add(c)
    session.flush()
    return c


def _videos(session, creator, values, feature=FEATURE, mv=MV):
    for i, v in enumerate(values):
        vid = Video(creator_id=creator.id, platform="tiktok",
                    external_id=f"{creator.handle}-{i}", source="provider")
        session.add(vid)
        session.flush()
        session.add(VideoFeature(
            video_id=vid.id, feature=feature, method_version=mv,
            method="deterministic", value_num=float(v), status="ok"))
    session.flush()


def _collection(session, members):
    c = CreatorCollection(name="Test cohort", purpose="research")
    session.add(c)
    session.flush()
    for m in members:
        session.add(CreatorCollectionMember(collection_id=c.id,
                                            creator_id=m.id))
    session.flush()
    return c


# ---------------------------------------------------------------------------
# the rank test
# ---------------------------------------------------------------------------

def test_the_rank_test_separates_two_obviously_different_groups():
    p = co._mann_whitney_p([1, 2, 3, 4, 5, 6], [20, 21, 22, 23, 24, 25])
    assert p is not None and p < 0.01


def test_the_rank_test_finds_nothing_between_identical_groups():
    p = co._mann_whitney_p([1, 2, 3, 4, 5, 6], [1, 2, 3, 4, 5, 6])
    assert p is not None and p > 0.9


def test_the_rank_test_refuses_samples_too_small_to_mean_anything():
    assert co._mann_whitney_p([1, 2], [3, 4, 5]) is None


def test_all_ties_produce_no_answer_rather_than_a_confident_one():
    """Zero variance has no rank information in it. Returning a p of
    1.0 would be defensible; returning None says "this test does not
    apply", which is the more useful thing to show a reader."""
    assert co._mann_whitney_p([5] * 6, [5] * 6) is None


def test_percentile_places_a_value_among_its_peers():
    assert co._percentile_of(5, [1, 2, 3, 4]) == 1.0
    assert co._percentile_of(0, [1, 2, 3, 4]) == 0.0
    assert co._percentile_of(2, [1, 2, 3, 4]) == pytest.approx(0.375)


# ---------------------------------------------------------------------------
# one creator must not speak for the cohort
# ---------------------------------------------------------------------------

def test_a_prolific_creator_cannot_outvote_the_rest(session):
    """The test that justifies the design.

    One creator posts 70 videos at a high value; eleven others post 5
    each at a low value. Pooled, the median is 100 and the cohort
    looks busy; per creator it is 1 and the cohort is quiet. The
    loud creator has to outnumber the rest COMBINED for the two to
    diverge, which is exactly the situation a real corpus produces
    when one account posts daily and the others post monthly.
    """
    owner = _creator(session, "owner", own=True)
    _videos(session, owner, [10.0] * 20)

    loud = _creator(session, "loud", followers=900000)
    _videos(session, loud, [100.0] * 70)
    quiet = [_creator(session, f"quiet{i}", followers=1000)
             for i in range(11)]
    for q in quiet:
        _videos(session, q, [1.0] * 5)

    coll = _collection(session, [loud, *quiet])
    rep = co.compare(session, coll.id, owner_platform="tiktok")

    pooled = statistics.median([100.0] * 70 + [1.0] * 55)
    assert pooled == 100.0, "the fixture must actually diverge"
    d = next(x for x in rep.differences if x.feature == FEATURE)

    assert d.cohort_median_of_medians == 1.0, (
        "the cohort median must come from creators, not publications")
    assert d.cohort_median_of_medians != pooled
    assert d.direction == "higher"
    assert d.owner_percentile is not None and d.owner_percentile > 0.9


def test_a_member_with_too_few_measured_videos_is_left_out(session):
    owner = _creator(session, "owner", own=True)
    _videos(session, owner, [5.0] * 10)
    thin = _creator(session, "thin")
    _videos(session, thin, [1.0, 2.0])           # below MIN_PUBLICATIONS
    fat = [_creator(session, f"fat{i}") for i in range(10)]
    for f in fat:
        _videos(session, f, [1.0] * 6)

    coll = _collection(session, [thin, *fat])
    rep = co.compare(session, coll.id)
    assert rep.composition["members"] == 11
    assert rep.composition["members_with_enough_measured"] == 10
    d = next(x for x in rep.differences if x.feature == FEATURE)
    assert d.cohort_members == 10


# ---------------------------------------------------------------------------
# what it may and may not claim
# ---------------------------------------------------------------------------

def test_nothing_is_ever_graded_above_observed(session):
    """A cross-creator difference has no outcome to be tested against,
    so it cannot reach Associated however small its q."""
    owner = _creator(session, "owner", own=True)
    _videos(session, owner, [100.0] * 20)
    peers = [_creator(session, f"p{i}") for i in range(12)]
    for p in peers:
        _videos(session, p, [1.0] * 6)

    rep = co.compare(session, _collection(session, peers).id)
    assert rep.differences
    for d in rep.differences:
        assert d.grade == "Observed", d.feature


def test_the_missing_retention_data_is_stated_every_time(session):
    owner = _creator(session, "owner", own=True)
    _videos(session, owner, [1.0] * 10)
    peers = [_creator(session, f"p{i}") for i in range(10)]
    for p in peers:
        _videos(session, p, [1.0] * 6)

    rep = co.compare(session, _collection(session, peers).id)
    joined = " ".join(rep.limitations).lower()
    assert "retention" in joined
    assert "never in what they achieve" in joined


def test_composition_travels_with_the_comparison(session):
    """M6 requires audience size, data age, completeness and platform
    on the same screen. The surest way is to make them impossible to
    fetch separately."""
    owner = _creator(session, "owner", own=True)
    _videos(session, owner, [1.0] * 10)
    peers = [_creator(session, f"p{i}", followers=100 * (i + 1))
             for i in range(10)]
    for p in peers:
        _videos(session, p, [1.0] * 6)

    rep = co.compare(session, _collection(session, peers).id)
    c = rep.composition
    assert c["follower_range"] == [100, 1000]
    assert c["platform"] == "tiktok"
    assert c["publications_in_cohort"] == 60
    assert c["publications_measured"] == 60
    assert c["minimum_required"] == co.MIN_MEMBERS
    assert len(rep.members) == 10
    assert all(m.followers is not None for m in rep.members)


def test_a_cohort_below_the_minimum_says_so_rather_than_refusing(session):
    """Ten is the bar M6 sets. Below it the comparison is still shown -
    a creator with nine peers should see something - but
    `meets_minimum` is false so the interface can mark it provisional.
    """
    owner = _creator(session, "owner", own=True)
    _videos(session, owner, [1.0] * 10)
    peers = [_creator(session, f"p{i}") for i in range(4)]
    for p in peers:
        _videos(session, p, [1.0] * 6)

    rep = co.compare(session, _collection(session, peers).id)
    assert rep.meets_minimum is False
    assert rep.composition["members"] == 4


def test_an_empty_cohort_explains_itself(session):
    _creator(session, "owner", own=True)
    rep = co.compare(session, _collection(session, []).id)
    assert rep.differences == []
    assert any("no cohort member" in x.lower() for x in rep.limitations)


def test_no_own_account_is_reported_not_crashed(session):
    peers = [_creator(session, f"p{i}") for i in range(10)]
    for p in peers:
        _videos(session, p, [1.0] * 6)
    rep = co.compare(session, _collection(session, peers).id)
    assert rep.differences == []
    assert any("nothing to compare" in x.lower() for x in rep.limitations)


def test_a_withheld_measurement_never_enters_the_comparison(session):
    """`vision_piano_or_keyboard_visible` is withheld. Convenience is
    not a reason to let it in here."""
    from server.features import visual_structure as vs

    compared = {f for f, _mv in list(vs.STRUCTURE_FEATURES)} | set(
        vs.VISION_FEATURES)
    assert not (compared & set(vs.WITHHELD_VISION))


def test_every_difference_carries_a_sentence_a_person_can_read(session):
    owner = _creator(session, "owner", own=True)
    _videos(session, owner, [9.0] * 12)
    peers = [_creator(session, f"p{i}") for i in range(10)]
    for p in peers:
        _videos(session, p, [1.0] * 6)

    rep = co.compare(session, _collection(session, peers).id)
    for d in rep.differences:
        assert d.plain and "_" not in d.plain, d.plain
        assert d.display_name != d.feature
        assert d.note
