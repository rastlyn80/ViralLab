"""Instagram media preservation: validation, reason classification, limits.

The headline test is `test_image_saved_as_mp4_is_rejected`. Three image posts
were downloaded as `.mp4` and recorded as `media_status='available'` because
the old validation only asked whether ffprobe succeeded - and ffprobe
succeeds on a JPEG, reporting `format_name=jpeg_pipe` and a video stream.
That is why the corpus showed 261 media files but only 258 fingerprints.
"""

from __future__ import annotations

import base64
import shutil
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from server.collectors import media_preservation as mp
from server.collectors.base import NormalizedVideo
from server.db.models import Base, Creator, Video

from tests.conftest import TEST_DB_URL, assert_disposable

DB_URL = TEST_DB_URL
pytestmark = pytest.mark.skipif(
    not DB_URL, reason="VIRALLAB_TEST_DB_URL not set")
if DB_URL:
    assert_disposable(DB_URL)

#: A valid 1x1 JPEG. ffprobe reports a video stream for this, which is
#: exactly why "ffprobe succeeded" was never a sufficient check.
TINY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
    "Hh0aHBwcJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPDI0Mv/AABEIAAEAAQMBIgACEQEDEQH/"
    "xAAfAAABBQEBAQEBAQAAAAAAAAABAgMEBQYHCAkKC//EALUQAAIBAwMCBAMFBQQEAAABfQEC"
    "AwAEEQUSITFBBhNRYQcicRQygZGhCCNCscEVUtHwJDNicoIJChYXGBkaJSYnKCkqNDU2Nzg5"
    "OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6g4SFhoeIiYqSk5SVlpeYmZqio6Sl"
    "pqeoqaqys7S1tre4ubrCw8TFxsfIycrS09TV1tfY2drh4uPk5ebn6Onq8fLz9PX29/j5+v/a"
    "AAwDAQACEQMRAD8A/v4oooA//9k=")


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(DB_URL, future=True)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    maker = sessionmaker(engine, expire_on_commit=False, future=True)
    with maker() as s:
        for t in ("metrics_snapshots", "video_master_links",
                  "publication_links", "videos", "local_masters",
                  "performances", "creators"):
            s.execute(text(f"truncate {t} restart identity cascade"))
        s.commit()
        yield s
        s.rollback()


@pytest.fixture
def creator(session):
    c = Creator(platform="instagram", handle="owner", external_id="ig-1",
                is_own_account=True)
    session.add(c)
    session.commit()
    return c


def add_video(session, creator, ext: str, **kw) -> Video:
    v = Video(creator_id=creator.id, platform="instagram", external_id=ext,
              source="official_api", media_status=kw.pop("media_status",
                                                         "absent"),
              posted_at=datetime.now(timezone.utc), **kw)
    session.add(v)
    session.commit()
    return v


class FakeAdapter:
    """Yields exactly what the real adapter yields: NormalizedVideo with the
    platform payload in `.raw`."""

    def __init__(self, items):
        self.items = items

    def fetch_account_videos(self, account_ref, **kw):
        for ext, raw in self.items:
            yield NormalizedVideo(
                external_id=ext, platform="instagram",
                media_url=raw.get("media_url"),
                media_url_expires=bool(raw.get("media_url")), raw=raw)


# ---------------------------------------------------------------------------
# validation - the defect that recorded images as preserved video
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not shutil.which("ffprobe"), reason="needs ffprobe")
def test_image_saved_as_mp4_is_rejected(tmp_path):
    """ffprobe SUCCEEDS on a JPEG and reports a video stream. Only the
    duration check separates it from real video."""
    p = tmp_path / "ig_fake.mp4"
    p.write_bytes(TINY_JPEG)
    is_video, dur = mp.probe(p)
    assert is_video is False, (
        "a JPEG renamed .mp4 passed validation - this is the exact bug that "
        "produced 261 files but 258 fingerprints")
    assert dur is None


@pytest.mark.skipif(not shutil.which("ffprobe"), reason="needs ffprobe")
def test_garbage_file_is_rejected(tmp_path):
    p = tmp_path / "ig_html.mp4"
    p.write_bytes(b"<html><body>error page</body></html>")
    assert mp.probe(p) == (False, None)


def test_empty_file_is_rejected(tmp_path):
    p = tmp_path / "ig_empty.mp4"
    p.write_bytes(b"")
    assert mp.probe(p)[0] is False


# ---------------------------------------------------------------------------
# reason classification - API restriction vs failure vs not-a-video
# ---------------------------------------------------------------------------

def test_non_video_types_are_unsupported_not_failures(session, creator,
                                                      tmp_path):
    add_video(session, creator, "img1")
    add_video(session, creator, "car1")
    adapter = FakeAdapter([
        ("img1", {"media_type": "IMAGE", "media_url": "http://x/1.jpg"}),
        ("car1", {"media_type": "CAROUSEL_ALBUM"}),
    ])
    res = mp.preserve_instagram_media(session, adapter, "ig-1",
                                      media_root=tmp_path)
    assert res.unsupported == 2
    assert res.failed == 0
    rows = {v.external_id: v for v in session.query(Video).all()}
    assert rows["img1"].media_status == "unsupported"
    assert rows["img1"].media_reason == "not_a_video:IMAGE"
    assert rows["car1"].media_reason == "not_a_video:CAROUSEL_ALBUM"


def test_missing_media_url_is_an_api_restriction(session, creator, tmp_path):
    add_video(session, creator, "v1")
    adapter = FakeAdapter([("v1", {"media_type": "VIDEO"})])
    res = mp.preserve_instagram_media(session, adapter, "ig-1",
                                      media_root=tmp_path)
    assert res.api_restricted == 1
    v = session.query(Video).one()
    assert v.media_status == "absent"
    assert v.media_reason == "api_restricted:no_media_url"


def test_download_failure_is_recorded_and_retryable(session, creator,
                                                    tmp_path, monkeypatch):
    add_video(session, creator, "v1")
    monkeypatch.setattr(mp, "download",
                        lambda url, dest, **kw: (False,
                                                 "download_failed:http_500"))
    adapter = FakeAdapter([("v1", {"media_type": "VIDEO",
                                   "media_url": "http://x/1.mp4"})])
    res = mp.preserve_instagram_media(session, adapter, "ig-1",
                                      media_root=tmp_path)
    assert res.failed == 1
    v = session.query(Video).one()
    assert v.media_reason == "download_failed:http_500"
    assert v.media_status == "absent"
    assert v.media_attempts == 1


def test_expired_url_is_classified_as_such(monkeypatch, tmp_path):
    """A dead signed CDN link is not a missing video - the next pass fetches
    a fresh URL."""
    import urllib.error

    def boom(*a, **kw):
        raise urllib.error.HTTPError("u", 403, "Forbidden", {}, None)

    monkeypatch.setattr(mp.urllib.request, "urlopen", boom)
    ok, reason = mp.download("http://x/1.mp4", tmp_path / "a.mp4")
    assert ok is False
    assert reason == "download_failed:url_expired"


def test_file_that_downloads_but_is_not_video_is_unsupported(
        session, creator, tmp_path, monkeypatch):
    add_video(session, creator, "v1")

    def fake_download(url, dest, **kw):
        dest.write_bytes(TINY_JPEG)
        return True, "ok"

    monkeypatch.setattr(mp, "download", fake_download)
    monkeypatch.setattr(mp, "probe", lambda p: (False, None))
    adapter = FakeAdapter([("v1", {"media_type": "VIDEO",
                                   "media_url": "http://x/1.mp4"})])
    res = mp.preserve_instagram_media(session, adapter, "ig-1",
                                      media_root=tmp_path)
    assert res.unsupported == 1
    v = session.query(Video).one()
    assert v.media_reason == "not_a_video:no_video_stream"
    assert not (tmp_path / "ig_v1.mp4").exists(), "bad file was not removed"


# ---------------------------------------------------------------------------
# limits and resumability
# ---------------------------------------------------------------------------

def test_attempts_are_bounded(session, creator, tmp_path, monkeypatch):
    """A permanently broken item must stop being retried."""
    v = add_video(session, creator, "v1")
    v.media_attempts = mp.MAX_ATTEMPTS
    session.commit()
    adapter = FakeAdapter([("v1", {"media_type": "VIDEO",
                                   "media_url": "http://x/1.mp4"})])
    calls = []
    monkeypatch.setattr(mp, "download",
                        lambda *a, **kw: (calls.append(1), (False, "x"))[1])
    res = mp.preserve_instagram_media(session, adapter, "ig-1",
                                      media_root=tmp_path)
    assert calls == [], "an exhausted item was retried"
    assert res.skipped_attempts == 1


def test_recent_failure_is_not_retried_immediately(session, creator,
                                                   tmp_path, monkeypatch):
    v = add_video(session, creator, "v1")
    v.media_attempts = 1
    v.media_checked_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    session.commit()
    adapter = FakeAdapter([("v1", {"media_type": "VIDEO",
                                   "media_url": "http://x/1.mp4"})])
    monkeypatch.setattr(mp, "download",
                        lambda *a, **kw: pytest.fail("retried too soon"))
    res = mp.preserve_instagram_media(session, adapter, "ig-1",
                                      media_root=tmp_path)
    assert res.considered == 0


def test_already_available_is_skipped(session, creator, tmp_path,
                                      monkeypatch):
    """Resumability: the existing archive is never re-downloaded."""
    add_video(session, creator, "v1", media_status="available",
              media_path=str(tmp_path / "ig_v1.mp4"), duration_s=12.0)
    (tmp_path / "ig_v1.mp4").write_bytes(b"x" * 100)
    adapter = FakeAdapter([("v1", {"media_type": "VIDEO",
                                   "media_url": "http://x/1.mp4"})])
    monkeypatch.setattr(mp, "download",
                        lambda *a, **kw: pytest.fail("re-downloaded"))
    res = mp.preserve_instagram_media(session, adapter, "ig-1",
                                      media_root=tmp_path)
    assert res.considered == 0
    assert res.stopped_reason == "nothing outstanding"


def test_storage_cap_stops_the_run(session, creator, tmp_path, monkeypatch):
    add_video(session, creator, "v1")
    (tmp_path / "existing.mp4").write_bytes(b"x" * 5000)
    adapter = FakeAdapter([("v1", {"media_type": "VIDEO",
                                   "media_url": "http://x/1.mp4"})])
    monkeypatch.setattr(mp, "download",
                        lambda *a, **kw: pytest.fail("downloaded past cap"))
    res = mp.preserve_instagram_media(session, adapter, "ig-1",
                                      media_root=tmp_path, storage_cap=1000)
    assert res.stopped_reason == "storage cap reached"
    v = session.query(Video).one()
    assert v.media_attempts == 0, (
        "hitting our own storage cap must not consume the item's attempts")


def test_duplicate_media_is_stored_once(session, creator, tmp_path,
                                        monkeypatch):
    add_video(session, creator, "v1")
    add_video(session, creator, "v2")
    payload = b"\x00VIDEO-BYTES" * 500

    def fake_download(url, dest, **kw):
        dest.write_bytes(payload)
        return True, "ok"

    monkeypatch.setattr(mp, "download", fake_download)
    monkeypatch.setattr(mp, "probe", lambda p: (True, 11.0))
    adapter = FakeAdapter([
        ("v1", {"media_type": "VIDEO", "media_url": "http://x/1.mp4"}),
        ("v2", {"media_type": "VIDEO", "media_url": "http://x/2.mp4"}),
    ])
    res = mp.preserve_instagram_media(session, adapter, "ig-1",
                                      media_root=tmp_path)
    assert res.saved == 1
    assert res.deduplicated == 1
    rows = {v.external_id: v for v in session.query(Video).all()}
    assert rows["v2"].media_path == rows["v1"].media_path
    assert rows["v2"].media_reason.startswith("deduplicated:")
    assert not (tmp_path / "ig_v2.mp4").exists()


def test_successful_save_backfills_duration(session, creator, tmp_path,
                                            monkeypatch):
    add_video(session, creator, "v1")
    monkeypatch.setattr(
        mp, "download",
        lambda url, dest, **kw: (dest.write_bytes(b"v" * 400), (True, "ok"))[1])
    monkeypatch.setattr(mp, "probe", lambda p: (True, 23.5))
    adapter = FakeAdapter([("v1", {"media_type": "VIDEO",
                                   "media_url": "http://x/1.mp4"})])
    res = mp.preserve_instagram_media(session, adapter, "ig-1",
                                      media_root=tmp_path)
    assert res.saved == 1
    v = session.query(Video).one()
    assert v.media_status == "available"
    assert v.media_reason is None
    assert v.duration_s == pytest.approx(23.5)


# ---------------------------------------------------------------------------
# coverage reporting
# ---------------------------------------------------------------------------

def test_coverage_excludes_unsupported_from_the_denominator(session,
                                                            creator):
    """Counting an image post as a missing video overstates the gap."""
    add_video(session, creator, "a", media_status="available",
              media_path="/x/a.mp4", duration_s=10.0)
    add_video(session, creator, "b", media_status="unsupported",
              media_reason="not_a_video:IMAGE")
    add_video(session, creator, "c", media_status="absent",
              media_reason="api_restricted:no_media_url")
    cov = mp.coverage(session)
    assert cov["total"] == 3
    assert cov["preservable"] == 2
    assert cov["preserved"] == 1
    assert cov["coverage_pct"] == pytest.approx(50.0)
    assert cov["by_reason_class"]["not_a_video"] == 1
    assert cov["by_reason_class"]["api_restricted"] == 1


# ---------------------------------------------------------------------------
# ownership scoping - the Phase 4 regression
#
# Before Phase 4 every row in `videos` belonged to the owner, so filtering
# by platform alone was equivalent to filtering by ownership. Competitor
# research broke that equivalence, and these tests exist because the
# difference is a policy violation rather than a wrong number: public
# metadata access is not permission to retain a competitor's media
# (docs/MULTIMODAL_PIPELINE.md).
# ---------------------------------------------------------------------------

@pytest.fixture
def competitor(session):
    c = Creator(platform="instagram", handle="someone_else",
                external_id="ig-other", is_own_account=False,
                source="manual_lookup")
    session.add(c)
    session.commit()
    return c


def test_competitor_media_is_never_downloaded(session, creator, competitor,
                                              tmp_path):
    """A researched competitor's reel must not be fetched or retained.

    The adapter offers a working media URL for both publications. Only the
    owner's is taken.
    """
    add_video(session, creator, "mine")
    add_video(session, competitor, "theirs")

    adapter = FakeAdapter([
        ("mine", {"media_type": "VIDEO"}),
        ("theirs", {"media_type": "VIDEO", "media_url": "http://x/t.mp4"}),
    ])
    mp.preserve_instagram_media(session, adapter, "ig-1",
                                media_root=tmp_path)

    rows = {v.external_id: v for v in session.query(Video).all()}
    # Untouched: no attempt, no status change, no reason recorded.
    assert rows["theirs"].media_status == "absent"
    assert rows["theirs"].media_reason is None
    assert (rows["theirs"].media_attempts or 0) == 0
    assert rows["theirs"].media_path is None
    assert not list(tmp_path.glob("ig_theirs*"))
    # The owner's was considered.
    assert rows["mine"].media_reason == "api_restricted:no_media_url"


def test_competitor_media_file_is_not_adopted_as_ours(session, creator,
                                                      competitor, tmp_path):
    """`sweep()` adopts orphaned files by external id.

    A file named after a competitor publication must be treated as an
    orphan and removed, not adopted into the archive, or the retention
    policy could be defeated by a filename.
    """
    (tmp_path / "ig_theirs.mp4").write_bytes(b"x" * 64)
    add_video(session, competitor, "theirs")

    res = mp.PreservationResult()
    mp.sweep(session, tmp_path, res)

    assert res.orphans_adopted == 0
    assert not (tmp_path / "ig_theirs.mp4").exists()
    v = session.query(Video).filter_by(external_id="theirs").one()
    assert v.media_status == "absent"


def test_coverage_excludes_competitors(session, creator, competitor):
    """A competitor's unpreserved reel is not a gap in our archive."""
    add_video(session, creator, "mine", media_status="available")
    for i in range(5):
        add_video(session, competitor, f"theirs{i}")

    cov = mp.coverage(session, "instagram")
    assert cov["total"] == 1
    assert cov["preservable"] == 1
    assert cov["preserved"] == 1
    assert cov["coverage_pct"] == 100.0


def test_backfill_hashes_ignores_competitors(session, creator, competitor,
                                             tmp_path):
    f = tmp_path / "ig_theirs.mp4"
    f.write_bytes(b"x" * 64)
    add_video(session, competitor, "theirs", media_status="available",
              media_path=str(f))
    assert mp.backfill_hashes(session) == 0
    v = session.query(Video).filter_by(external_id="theirs").one()
    assert v.media_sha256 is None
