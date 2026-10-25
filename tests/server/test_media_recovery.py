"""Crash recovery, orphan handling, deduplication and cap behaviour.

Separate from test_media_preservation.py, which covers validation and the
reason taxonomy. These tests cover what happens when a pass does not finish
cleanly - the cases an independent review flagged as unverified.

Each test names the defect it pins.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from server.collectors import media_preservation as mp
from server.collectors.base import NormalizedVideo
from server.db.models import Base, Creator, Video

from tests.conftest import TEST_DB_URL, assert_disposable
from tests.server.test_media_preservation import TINY_JPEG

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
              source="official_api",
              media_status=kw.pop("media_status", "absent"),
              posted_at=datetime.now(timezone.utc), **kw)
    session.add(v)
    session.commit()
    return v


class FakeAdapter:
    def __init__(self, items):
        self.items = items

    def fetch_account_videos(self, account_ref, **kw):
        for ext, raw in self.items:
            yield NormalizedVideo(
                external_id=ext, platform="instagram",
                media_url=raw.get("media_url"), raw=raw)


def saving_download(payload=b"video-bytes" * 40):
    def _dl(url, dest, **kw):
        dest.write_bytes(payload)
        return True, "ok"
    return _dl


# ---------------------------------------------------------------------------
# transaction boundaries and crash recovery
# ---------------------------------------------------------------------------

def test_state_is_committed_per_item_not_per_pass(session, creator, tmp_path,
                                                  monkeypatch):
    """A crash mid-pass must not discard items already decided.

    The first version committed once at the end, so a crash lost every
    status update and attempt increment while leaving the downloaded files
    on disk. The next run then saw unattempted rows beside orphaned files,
    and a repeatedly-crashing run could re-download the same media forever
    without ever exhausting its attempts.
    """
    for ext in ("v1", "v2", "v3"):
        add_video(session, creator, ext)

    calls = {"n": 0}

    def exploding(url, dest, **kw):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("process died mid-pass")
        dest.write_bytes(b"video-" + url.encode())
        return True, "ok"

    monkeypatch.setattr(mp, "download", exploding)
    monkeypatch.setattr(mp, "probe", lambda p: (True, 10.0))
    adapter = FakeAdapter([
        (e, {"media_type": "VIDEO", "media_url": f"http://x/{e}.mp4"})
        for e in ("v1", "v2", "v3")])

    with pytest.raises(RuntimeError):
        mp.preserve_instagram_media(session, adapter, "ig-1",
                                    media_root=tmp_path)

    session.rollback()
    rows = {v.external_id: v for v in session.query(Video).all()}
    done = [e for e, v in rows.items() if v.media_status == "available"]
    assert len(done) == 2, (
        f"only {len(done)} item(s) survived the crash; per-item commits are "
        f"not in effect")
    assert all(rows[e].media_attempts == 1 for e in done)
    assert all(rows[e].media_sha256 for e in done)


def test_a_database_failure_on_one_item_does_not_abort_the_pass(
        session, creator, tmp_path, monkeypatch):
    add_video(session, creator, "v1")
    add_video(session, creator, "v2")
    # Distinct bytes per item, or the second is legitimately deduplicated
    # and this test would be measuring the wrong thing.
    monkeypatch.setattr(
        mp, "download",
        lambda url, dest, **kw: (dest.write_bytes(url.encode() * 40),
                                 (True, "ok"))[1])
    monkeypatch.setattr(mp, "probe", lambda p: (True, 9.0))

    calls = {"n": 0}
    real_commit = mp.Session.commit

    def flaky(self):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("deadlock")
        return real_commit(self)

    monkeypatch.setattr(mp.Session, "commit", flaky, raising=False)
    adapter = FakeAdapter([
        (e, {"media_type": "VIDEO", "media_url": f"http://x/{e}.mp4"})
        for e in ("v1", "v2")])
    res = mp.preserve_instagram_media(session, adapter, "ig-1",
                                      media_root=tmp_path)
    assert res.considered == 2, "a commit failure aborted the whole pass"
    assert res.saved == 2


# ---------------------------------------------------------------------------
# orphans and partial downloads
# ---------------------------------------------------------------------------

def test_orphaned_file_is_adopted_not_re_downloaded(session, creator,
                                                    tmp_path, monkeypatch):
    """A file written by a pass that died before committing is good data."""
    add_video(session, creator, "v1")
    (tmp_path / "ig_v1.mp4").write_bytes(b"orphan-video" * 20)
    monkeypatch.setattr(mp, "probe", lambda p: (True, 14.0))
    monkeypatch.setattr(
        mp, "download",
        lambda *a, **kw: pytest.fail("re-downloaded an orphan instead of "
                                     "adopting it"))
    adapter = FakeAdapter([("v1", {"media_type": "VIDEO",
                                   "media_url": "http://x/1.mp4"})])
    res = mp.preserve_instagram_media(session, adapter, "ig-1",
                                      media_root=tmp_path)
    assert res.orphans_adopted == 1
    v = session.query(Video).one()
    assert v.media_status == "available"
    assert v.media_sha256, "an adopted file must get its digest recorded"
    assert v.duration_s == pytest.approx(14.0)


def test_orphan_with_no_publication_is_removed(session, creator, tmp_path):
    """Media for a publication that no longer exists is not kept forever."""
    stray = tmp_path / "ig_9999999.mp4"
    stray.write_bytes(b"x" * 50)
    res = mp.PreservationResult()
    mp.sweep(session, tmp_path, res)
    assert res.orphans_removed == 1
    assert not stray.exists()


def test_unplayable_orphan_is_deleted_not_adopted(session, creator, tmp_path,
                                                  monkeypatch):
    add_video(session, creator, "v1")
    f = tmp_path / "ig_v1.mp4"
    f.write_bytes(TINY_JPEG)
    monkeypatch.setattr(mp, "probe", lambda p: (False, None))
    res = mp.PreservationResult()
    mp.sweep(session, tmp_path, res)
    assert res.orphans_adopted == 0
    assert not f.exists(), "an unplayable orphan was adopted as media"


def test_stale_part_files_are_swept(session, creator, tmp_path):
    """A download killed partway leaves a .part. It is never resumed - the
    next attempt needs a fresh URL anyway."""
    (tmp_path / "ig_v1.mp4.part").write_bytes(b"half")
    (tmp_path / "ig_v2.mp4.part").write_bytes(b"half")
    res = mp.PreservationResult()
    mp.sweep(session, tmp_path, res)
    assert res.part_files_removed == 2
    assert not list(tmp_path.glob("*.part"))


def test_failed_download_leaves_nothing_behind(tmp_path, monkeypatch):
    class Boom:
        def __enter__(self):
            raise OSError("connection reset")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mp.urllib.request, "urlopen", lambda *a, **k: Boom())
    ok, reason = mp.download("http://x/1.mp4", tmp_path / "ig_v1.mp4")
    assert ok is False
    assert reason.startswith("download_failed:")
    assert list(tmp_path.iterdir()) == [], "a partial file was left behind"


def test_an_available_file_is_not_treated_as_an_orphan(session, creator,
                                                       tmp_path):
    f = tmp_path / "ig_v1.mp4"
    f.write_bytes(b"kept" * 50)
    add_video(session, creator, "v1", media_status="available",
              media_path=str(f), duration_s=10.0)
    res = mp.PreservationResult()
    mp.sweep(session, tmp_path, res)
    assert (res.orphans_adopted, res.orphans_removed) == (0, 0)
    assert f.exists()


# ---------------------------------------------------------------------------
# deduplication without re-reading the archive
# ---------------------------------------------------------------------------

def test_dedup_index_comes_from_the_database_not_the_disk(
        session, creator, tmp_path, monkeypatch):
    """Hashing every preserved file per pass meant reading 736 MB daily."""
    payload = b"IDENTICAL" * 400
    digest = hashlib.sha256(payload).hexdigest()

    owner = add_video(session, creator, "v1", media_status="available",
                      media_path=str(tmp_path / "ig_v1.mp4"), duration_s=9.0)
    owner.media_sha256 = digest
    session.commit()
    (tmp_path / "ig_v1.mp4").write_bytes(payload)
    add_video(session, creator, "v2")

    reads = {"n": 0}
    real = mp.sha256_of

    def counting(path):
        reads["n"] += 1
        return real(path)

    monkeypatch.setattr(mp, "sha256_of", counting)
    monkeypatch.setattr(mp, "download", saving_download(payload))
    monkeypatch.setattr(mp, "probe", lambda p: (True, 9.0))
    adapter = FakeAdapter([("v2", {"media_type": "VIDEO",
                                   "media_url": "http://x/2.mp4"})])
    res = mp.preserve_instagram_media(session, adapter, "ig-1",
                                      media_root=tmp_path)
    assert res.deduplicated == 1
    assert reads["n"] == 1, (
        f"hashed {reads['n']} file(s); only the newly downloaded one needs "
        f"reading")
    rows = {v.external_id: v for v in session.query(Video).all()}
    assert rows["v2"].media_path == rows["v1"].media_path
    assert rows["v2"].media_sha256 == digest
    assert not (tmp_path / "ig_v2.mp4").exists()


def test_a_deduplicated_row_is_not_itself_a_dedup_target(session, creator,
                                                         tmp_path,
                                                         monkeypatch):
    """Otherwise a third copy could point at a row that owns no file."""
    payload = b"SAME" * 200
    digest = hashlib.sha256(payload).hexdigest()
    a = add_video(session, creator, "v1", media_status="available",
                  media_path=str(tmp_path / "ig_v1.mp4"))
    a.media_sha256 = digest
    b = add_video(session, creator, "v2", media_status="available",
                  media_path=str(tmp_path / "ig_v1.mp4"),
                  media_reason="deduplicated:v1")
    b.media_sha256 = digest
    session.commit()
    (tmp_path / "ig_v1.mp4").write_bytes(payload)
    add_video(session, creator, "v3")

    monkeypatch.setattr(mp, "download", saving_download(payload))
    monkeypatch.setattr(mp, "probe", lambda p: (True, 9.0))
    adapter = FakeAdapter([("v3", {"media_type": "VIDEO",
                                   "media_url": "http://x/3.mp4"})])
    mp.preserve_instagram_media(session, adapter, "ig-1",
                                media_root=tmp_path)
    rows = {v.external_id: v for v in session.query(Video).all()}
    assert rows["v3"].media_reason == "deduplicated:v1", (
        "the third copy was pointed at a row that owns no file")


def test_backfill_hashes_records_digests_for_existing_files(session, creator,
                                                            tmp_path):
    f = tmp_path / "ig_v1.mp4"
    f.write_bytes(b"legacy" * 100)
    add_video(session, creator, "v1", media_status="available",
              media_path=str(f), duration_s=11.0)
    assert mp.backfill_hashes(session) == 1
    assert session.query(Video).one().media_sha256
    assert mp.backfill_hashes(session) == 0, "backfill is not idempotent"


# ---------------------------------------------------------------------------
# storage cap
# ---------------------------------------------------------------------------

def test_storage_cap_does_not_start_the_retry_cooldown(session, creator,
                                                       tmp_path, monkeypatch):
    """Hitting OUR cap must not push the item into the 20-hour cooldown, or
    raising the cap would not take effect until the next day."""
    add_video(session, creator, "v1")
    (tmp_path / "filler.mp4").write_bytes(b"x" * 5000)
    adapter = FakeAdapter([("v1", {"media_type": "VIDEO",
                                   "media_url": "http://x/1.mp4"})])
    monkeypatch.setattr(mp, "download",
                        lambda *a, **kw: pytest.fail("downloaded past cap"))
    res = mp.preserve_instagram_media(session, adapter, "ig-1",
                                      media_root=tmp_path, storage_cap=1000)
    assert res.stopped_reason == "storage cap reached"
    v = session.query(Video).one()
    assert v.media_attempts == 0
    assert v.media_checked_at is None, (
        "our own cap started the item's cooldown clock")
    assert mp._retryable(v, datetime.now(timezone.utc)) is True


# ---------------------------------------------------------------------------
# idempotency
# ---------------------------------------------------------------------------

def test_second_pass_is_a_no_op(session, creator, tmp_path, monkeypatch):
    add_video(session, creator, "v1")
    monkeypatch.setattr(mp, "download", saving_download())
    monkeypatch.setattr(mp, "probe", lambda p: (True, 12.0))
    adapter = FakeAdapter([("v1", {"media_type": "VIDEO",
                                   "media_url": "http://x/1.mp4"})])
    first = mp.preserve_instagram_media(session, adapter, "ig-1",
                                        media_root=tmp_path)
    assert first.saved == 1
    monkeypatch.setattr(mp, "download",
                        lambda *a, **kw: pytest.fail("re-downloaded"))
    second = mp.preserve_instagram_media(session, adapter, "ig-1",
                                         media_root=tmp_path)
    assert second.saved == 0
    assert second.stopped_reason == "nothing outstanding"
    assert len(list(tmp_path.glob("ig_*.mp4"))) == 1
    assert session.query(Video).count() == 1


def test_caller_supplied_items_avoid_a_second_pagination(session, creator,
                                                         tmp_path,
                                                         monkeypatch):
    """One daily pass should paginate the account once, not twice."""
    add_video(session, creator, "v1")
    monkeypatch.setattr(mp, "download", saving_download())
    monkeypatch.setattr(mp, "probe", lambda p: (True, 8.0))

    class Exploding:
        def fetch_account_videos(self, *a, **kw):
            pytest.fail("paginated the account a second time")

    items = list(FakeAdapter(
        [("v1", {"media_type": "VIDEO",
                 "media_url": "http://x/1.mp4"})]).fetch_account_videos("x"))
    res = mp.preserve_instagram_media(session, Exploding(), "ig-1",
                                      media_root=tmp_path, items=items)
    assert res.saved == 1
