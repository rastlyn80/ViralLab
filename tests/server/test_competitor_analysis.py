"""M2: a competitor file is deleted for a complete analysis, or not at all.

The defect these exist for was real and silent. `analyse_held` counted
stored feature rows across all three method versions at once and
released the file as soon as the total was non-zero, so a publication
whose deterministic pass succeeded and whose background and OCR passes
both failed was reported as analysed, lost the only copy of its input,
and left nothing behind to say two thirds of the work was missing. The
retry it needed had become impossible.

Nothing failed loudly when that happened — the counts went up, the
audit stayed green, and the gap only shows if you go looking for rows
that are not there. So these tests drive the stages directly rather
than through real extraction, and assert on which file still exists.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from server.collectors import transient
from server.db.models import Base, Creator, Video, VideoFeature
from server.features import competitor
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
        s.execute(text("truncate transient_media, video_features, videos, "
                       "creators restart identity cascade"))
        s.commit()
        yield s
        s.rollback()


@pytest.fixture
def quarantine(tmp_path, monkeypatch):
    root = tmp_path / "media_transient"
    root.mkdir()
    monkeypatch.setattr(transient, "QUARANTINE_ROOT", root)
    return root


def _held(session, quarantine, *, name="competitor_1.mp4", now=NOW,
          ttl=transient.TTL):
    """A competitor publication with a live file in quarantine."""
    # One creator per publication, with a handle derived from the file
    # name: `creators` has a unique (platform, handle) index, so a shared
    # literal would make any test that holds two publications fail on
    # the fixture rather than on the thing it is testing.
    c = Creator(platform="tiktok", handle=f"someone_else_{name}",
                is_own_account=False, source="discovery")
    session.add(c)
    session.flush()
    v = Video(creator_id=c.id, platform="tiktok",
              external_id=f"x{name}", source="provider")
    session.add(v)
    session.flush()
    p = quarantine / name
    p.write_bytes(b"not really a video")
    row = transient.register(session, video_id=v.id, path=p,
                             source="apify:test",
                             permission_basis="test", now=now, ttl=ttl)
    return row, v, p


def _store(session, video_id: int, method_version: str,
           feature: str = "f") -> None:
    session.add(VideoFeature(
        video_id=video_id, feature=feature, method_version=method_version,
        method="deterministic", value_num=1.0, status="ok"))
    session.flush()


def _stage_mv(name: str) -> str:
    return next(mv for n, _r, mv in competitor.STAGES if n == name)


def _runners_that_do_nothing(monkeypatch):
    """Replace every extractor with a no-op.

    The point of these tests is the bookkeeping around extraction, not
    extraction itself; letting the real runners touch an 18-byte "mp4"
    would measure ffmpeg's error handling instead.
    """
    class _Result:
        done = 0
        failed = 0
        features_written = 0
        compute_seconds = 0.0
        errors: list = []

    monkeypatch.setattr(competitor, "STAGES", tuple(
        (name, lambda *a, **k: _Result(), mv)
        for name, _runner, mv in competitor.STAGES))


# ---------------------------------------------------------------------------
# completion() reads stages, not totals
# ---------------------------------------------------------------------------

def test_one_stage_is_not_three(session, quarantine):
    """The defect, stated as an assertion.

    Rows exist, so a count across all method versions is non-zero — and
    the publication is still two thirds unanalysed.
    """
    _row, v, _p = _held(session, quarantine)
    _store(session, v.id, _stage_mv("deterministic"))

    state = competitor.completion(session, v.id)
    assert state["state"] == "partial"
    assert state["stages_done"] == ["deterministic"]
    assert state["stages_missing"] == ["background", "ocr"]


def test_all_stages_present_is_complete(session, quarantine):
    _row, v, _p = _held(session, quarantine)
    for name, _r, mv in competitor.STAGES:
        _store(session, v.id, mv, feature=name)
    assert competitor.completion(session, v.id)["state"] == "complete"


def test_no_stages_is_failed(session, quarantine):
    _row, v, _p = _held(session, quarantine)
    assert competitor.completion(session, v.id)["state"] == "failed"


def test_a_row_under_the_wrong_method_version_counts_for_nothing(
        session, quarantine):
    """Stage identity is the method version. A feature stored under some
    other version is not evidence that this stage ran."""
    _row, v, _p = _held(session, quarantine)
    _store(session, v.id, "some-other-method-v9")
    assert competitor.completion(session, v.id)["state"] == "failed"


def test_expired_downgrades_anything_unfinished_but_not_completion(
        session, quarantine):
    _row, v, _p = _held(session, quarantine)
    _store(session, v.id, _stage_mv("deterministic"))
    assert competitor.completion(session, v.id,
                                 expired=True)["state"] == "expired"

    for name, _r, mv in competitor.STAGES:
        _store(session, v.id, mv, feature=name)
    # Finished before the deadline mattered: the evidence is stored and
    # the deadline is now irrelevant to this publication.
    assert competitor.completion(session, v.id,
                                 expired=True)["state"] == "complete"


# ---------------------------------------------------------------------------
# what that means for the file
# ---------------------------------------------------------------------------

def test_a_partial_analysis_keeps_the_file_and_says_so(
        session, quarantine, monkeypatch):
    """The regression. Before the fix this released the file and
    reported the publication as analysed."""
    _runners_that_do_nothing(monkeypatch)
    row, v, p = _held(session, quarantine)
    _store(session, v.id, _stage_mv("deterministic"))

    out = competitor.analyse_held(session, limit=10, now=NOW)

    assert out.partial == 1
    assert out.complete == 0
    assert out.released == 0
    assert p.exists(), "the only copy of the input was deleted"
    assert row.deleted_at is None
    assert out.by_video[v.id]["stages_missing"] == ["background", "ocr"]
    err = next(e for e in out.errors if e.get("video_id") == v.id)
    assert err["state"] == "partial"
    assert "retried before its TTL" in err["error"]


def test_a_partial_analysis_keeps_the_features_it_did_get(
        session, quarantine, monkeypatch):
    """A partial result is evidence. Discarding it would mean re-paying
    for the work that succeeded."""
    _runners_that_do_nothing(monkeypatch)
    _row, v, _p = _held(session, quarantine)
    _store(session, v.id, _stage_mv("deterministic"))

    competitor.analyse_held(session, limit=10, now=NOW)

    assert competitor._rows_for_stage(
        session, v.id, _stage_mv("deterministic")) == 1


def test_a_complete_analysis_releases_the_file(session, quarantine,
                                               monkeypatch):
    _runners_that_do_nothing(monkeypatch)
    row, v, p = _held(session, quarantine)
    for name, _r, mv in competitor.STAGES:
        _store(session, v.id, mv, feature=name)

    out = competitor.analyse_held(session, limit=10, now=NOW)

    assert out.complete == 1 and out.released == 1
    assert out.partial == 0 and out.failed == 0
    assert not p.exists()
    assert row.delete_reason == "features_persisted"


def test_an_already_complete_publication_is_not_re_extracted(
        session, quarantine, monkeypatch):
    """"Safe to re-run" should mean cheap as well as correct."""
    _runners_that_do_nothing(monkeypatch)
    _row, v, _p = _held(session, quarantine)
    for name, _r, mv in competitor.STAGES:
        _store(session, v.id, mv, feature=name)

    out = competitor.analyse_held(session, limit=10, now=NOW)
    assert out.by_video[v.id]["re_extracted"] is False
    assert out.by_stage == {}, "no stage should have run"


def test_a_total_failure_keeps_the_file_for_retry(session, quarantine,
                                                  monkeypatch):
    _runners_that_do_nothing(monkeypatch)
    _row, v, p = _held(session, quarantine)

    out = competitor.analyse_held(session, limit=10, now=NOW)

    assert out.failed == 1 and out.released == 0
    assert p.exists()
    assert out.by_video[v.id]["state"] == "failed"


def test_an_expired_file_is_reported_expired_and_never_claimed_analysed(
        session, quarantine, monkeypatch):
    """Past the deadline the reaper owns the file. Whatever features
    exist are kept, and the publication stays incomplete."""
    _runners_that_do_nothing(monkeypatch)
    _row, v, _p = _held(session, quarantine)
    _store(session, v.id, _stage_mv("deterministic"))

    out = competitor.analyse_held(session, limit=10,
                                  now=NOW + timedelta(hours=7))

    assert out.expired == 1
    assert out.complete == 0 and out.partial == 0
    assert out.released == 0, "releasing here would be the reaper's job"
    err = next(e for e in out.errors if e.get("video_id") == v.id)
    assert "deadline has passed" in err["error"]


def test_the_states_are_mutually_exclusive_and_cover_every_publication(
        session, quarantine, monkeypatch):
    """Three publications, three different states, no double counting."""
    _runners_that_do_nothing(monkeypatch)
    _r1, done, _p1 = _held(session, quarantine, name="competitor_1.mp4")
    _r2, half, _p2 = _held(session, quarantine, name="competitor_2.mp4")
    _r3, _none, _p3 = _held(session, quarantine, name="competitor_3.mp4")
    for name, _r, mv in competitor.STAGES:
        _store(session, done.id, mv, feature=name)
    _store(session, half.id, _stage_mv("ocr"))

    out = competitor.analyse_held(session, limit=10, now=NOW)

    assert (out.complete, out.partial, out.failed, out.expired) == (1, 1, 1, 0)
    assert out.complete + out.partial + out.failed + out.expired \
        == out.considered
    assert set(out.by_video) == {done.id, half.id, _none.id}
    assert out.fully_analysed == 1


# ---------------------------------------------------------------------------
# and for the coverage report
# ---------------------------------------------------------------------------

def test_coverage_counts_only_fully_analysed_publications(session,
                                                          quarantine):
    """The same mistake, one level up: coverage used to count anything
    with a deterministic row as analysed."""
    _r1, done, _p1 = _held(session, quarantine, name="competitor_1.mp4")
    _r2, half, _p2 = _held(session, quarantine, name="competitor_2.mp4")
    for name, _r, mv in competitor.STAGES:
        _store(session, done.id, mv, feature=name)
    _store(session, half.id, _stage_mv("deterministic"))

    cov = competitor.coverage(session)
    assert cov["competitor_publications_analysed"] == 1
    assert cov["competitor_publications_partial"] == 1
    assert cov["competitor_creators_analysed"] == 1
    assert sorted(cov["counted_as_analysed_only_if"]) == sorted(
        mv for _n, _r, mv in competitor.STAGES)
