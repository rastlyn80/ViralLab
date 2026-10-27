"""M2: competitor media is transient, and something enforces it.

`docs/MULTIMODAL_PIPELINE.md` §0 has stated since Phase 4 that competitor
media is fetched, analysed and deleted rather than accumulated. It was
stated before anything checked it, and a collector was later found
violating it. These tests are the check.

Every property here fails silently if it breaks. A file that outlives its
deadline looks exactly like a file that has not reached it yet; a
quarantine directory inside `MEDIA_ROOT` looks exactly like one outside
it until someone requests a signed URL.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from server.collectors import transient
from server.db.models import Base, Creator, TransientMedia, Video
from tests.conftest import TEST_DB_URL, assert_disposable

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)

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
        s.execute(text("truncate transient_media, videos, creators "
                       "restart identity cascade"))
        s.commit()
        yield s
        s.rollback()


@pytest.fixture
def quarantine(tmp_path, monkeypatch):
    root = tmp_path / "media_transient"
    root.mkdir()
    monkeypatch.setattr(transient, "QUARANTINE_ROOT", root)
    return root


def _competitor_video(session) -> Video:
    c = Creator(platform="tiktok", handle="someone_else",
                is_own_account=False, source="discovery")
    session.add(c)
    session.flush()
    v = Video(creator_id=c.id, platform="tiktok", external_id="x1",
              source="provider")
    session.add(v)
    session.flush()
    return v


def _file(root: Path, name: str = "competitor_1.mp4") -> Path:
    p = root / name
    p.write_bytes(b"not really a video")
    return p


# ---------------------------------------------------------------------------
# where the bytes may live
# ---------------------------------------------------------------------------

def test_quarantine_inside_media_root_is_refused(tmp_path):
    """The structural barrier, not the handler-level one.

    `server/api/app.py` already contains a crafted `media_path` to
    `MEDIA_ROOT`. If quarantine sat inside that directory, the only thing
    between competitor bytes and a signed URL would be the
    `is_own_account` check in the frames endpoint — one check. Keeping it
    outside means the containment logic refuses them too.
    """
    media_root = tmp_path / "media"
    media_root.mkdir()
    inside = media_root / "transient"
    inside.mkdir()
    with pytest.raises(transient.QuarantineViolation, match="inside"):
        transient.assert_outside_media_root(inside, media_root)
    # The sibling arrangement is what should be allowed.
    transient.assert_outside_media_root(tmp_path / "media_transient",
                                        media_root)


def test_media_root_itself_is_refused(tmp_path):
    media_root = tmp_path / "media"
    media_root.mkdir()
    with pytest.raises(transient.QuarantineViolation):
        transient.assert_outside_media_root(media_root, media_root)


def test_register_refuses_a_path_under_media_root(session, tmp_path):
    media_root = tmp_path / "media"
    (media_root / "transient").mkdir(parents=True)
    v = _competitor_video(session)
    bad = media_root / "transient" / "competitor_1.mp4"
    bad.write_bytes(b"x")
    with pytest.raises(transient.QuarantineViolation):
        transient.register(session, video_id=v.id, path=bad,
                           source="apify:test", permission_basis="test",
                           media_root=media_root)


# ---------------------------------------------------------------------------
# the deadline
# ---------------------------------------------------------------------------

def test_registration_always_carries_an_expiry(session, quarantine):
    v = _competitor_video(session)
    row = transient.register(
        session, video_id=v.id, path=_file(quarantine),
        source="apify:clockworks/free-tiktok-scraper",
        permission_basis="provider delivered under its own terms",
        now=NOW)
    assert row.expires_at == NOW + transient.TTL
    assert row.sha256 and row.size_bytes == len(b"not really a video")
    assert row.deleted_at is None


def test_sweep_deletes_an_expired_file_and_records_why(session, quarantine):
    v = _competitor_video(session)
    p = _file(quarantine)
    transient.register(session, video_id=v.id, path=p, source="apify:test",
                       permission_basis="test", now=NOW)
    out = transient.sweep(session, now=NOW + timedelta(hours=7))
    assert out["expired"] == 1
    assert not p.exists(), "an expired competitor file survived the sweep"
    row = session.scalar(select(TransientMedia))
    assert row.deleted_at is not None
    assert row.delete_reason == "expired"


def test_sweep_leaves_a_file_inside_its_window(session, quarantine):
    v = _competitor_video(session)
    p = _file(quarantine)
    transient.register(session, video_id=v.id, path=p, source="apify:test",
                       permission_basis="test", now=NOW)
    out = transient.sweep(session, now=NOW + timedelta(hours=1))
    assert out["expired"] == 0
    assert p.exists(), "a file well inside its TTL was deleted"


def test_an_unregistered_file_is_deleted_once_it_is_past_the_grace(
        session, quarantine):
    """The crash case: bytes on disk that no row knows about.

    There is no basis on which to keep a file nobody recorded and no
    expiry it could be measured against, so it goes — but not before
    `ORPHAN_GRACE`, because a file that young may be mid-acquisition
    with its row not yet committed. Walking only the ledger would never
    see it at all.
    """
    import os
    stray = quarantine / "competitor_999.mp4"
    stray.write_bytes(b"orphan")
    old = (NOW - transient.ORPHAN_GRACE - timedelta(seconds=30)).timestamp()
    os.utime(stray, (old, old))
    out = transient.sweep(session, now=NOW)
    assert out["orphans_deleted"] == 1
    assert not stray.exists()


def test_a_file_reappearing_at_a_deleted_rows_path_is_still_an_orphan(
        session, quarantine):
    """The worst state this feature can reach, and it happened for real.

    Quarantine paths are derived from the video id, so a re-acquired
    publication writes to the same path its previous (now deleted) row
    recorded. The sweep built its "known files" set from ALL rows, so the
    new file looked accounted-for and was skipped — leaving bytes on disk
    under an audit record stating they had been removed. A false audit is
    strictly worse than no audit.
    """
    v = _competitor_video(session)
    p = _file(quarantine)
    row = transient.register(session, video_id=v.id, path=p,
                             source="apify:test", permission_basis="test",
                             now=NOW)
    transient.release(session, row, reason="expired", now=NOW)
    assert not p.exists()

    # Re-acquired: same derived path, and the transaction that would have
    # recorded it rolled back, so no live row claims it.
    #
    # The mtime is set explicitly because these tests drive a simulated
    # clock (NOW is in 2026-10-26) while the filesystem stamps real wall
    # time. Left alone, the file reads as newer than the simulated sweep
    # and is spared by ORPHAN_GRACE for a reason that has nothing to do
    # with what is under test.
    import os
    p.write_bytes(b"re-acquired bytes")
    aged = (NOW - transient.ORPHAN_GRACE - timedelta(minutes=1)).timestamp()
    os.utime(p, (aged, aged))
    out = transient.sweep(session,
                          now=NOW + transient.ORPHAN_GRACE
                          + timedelta(minutes=1))
    assert out["orphans_deleted"] == 1, (
        "a file at a deleted row's path was treated as known")
    assert not p.exists()
    assert transient.audit(
        session, now=NOW + transient.ORPHAN_GRACE + timedelta(minutes=1)
    )["guarantee_holds"] is True


def test_the_audit_sees_a_file_a_deleted_row_claims_to_have_removed(
        session, quarantine):
    """`audit()` must not be fooled the same way `sweep()` was."""
    v = _competitor_video(session)
    p = _file(quarantine)
    row = transient.register(session, video_id=v.id, path=p,
                             source="apify:test", permission_basis="test",
                             now=NOW)
    transient.release(session, row, reason="expired", now=NOW)
    p.write_bytes(b"back again")
    state = transient.audit(session, now=NOW + timedelta(minutes=1))
    assert state["unregistered_files"] == 1
    assert state["guarantee_holds"] is False


def test_a_very_new_unregistered_file_is_left_alone(session, quarantine):
    """A file mid-acquisition must survive a concurrent sweep.

    MEASURED 2026-10-27: a 25-file batch committed its rows only at the
    end, so for the duration of the batch every downloaded file was
    invisible to the reaper's session and 17 of 25 were deleted as
    orphans. All 25 had been paid for. Per-file commit narrows the
    window; this grace period closes it, at the cost of delaying the
    deletion of genuinely orphaned files by minutes against a six-hour
    ceiling.
    """
    fresh = quarantine / "competitor_in_flight.mp4"
    fresh.write_bytes(b"being downloaded right now")
    out = transient.sweep(session, now=NOW)
    assert out["orphans_deleted"] == 0
    assert out["recent_files_left_alone"] == 1
    assert fresh.exists(), "a file mid-acquisition was deleted"


def test_an_old_unregistered_file_is_still_deleted(session, quarantine):
    """The grace period must not become a loophole."""
    import os
    stale = quarantine / "competitor_abandoned.mp4"
    stale.write_bytes(b"left behind")
    old = (NOW - transient.ORPHAN_GRACE - timedelta(minutes=5)).timestamp()
    os.utime(stale, (old, old))
    out = transient.sweep(session, now=NOW)
    assert out["orphans_deleted"] == 1
    assert not stale.exists()


def test_a_row_whose_file_vanished_is_reconciled_not_left_live(
        session, quarantine):
    v = _competitor_video(session)
    p = _file(quarantine)
    transient.register(session, video_id=v.id, path=p, source="apify:test",
                       permission_basis="test", now=NOW)
    p.unlink()                       # crashed mid-flight
    out = transient.sweep(session, now=NOW + timedelta(minutes=5))
    assert out["rows_reconciled"] == 1
    row = session.scalar(select(TransientMedia))
    assert row.delete_reason == "crash_recovery"
    assert out["still_live"] == 0


def test_release_is_idempotent_and_keeps_the_first_reason(session,
                                                          quarantine):
    v = _competitor_video(session)
    p = _file(quarantine)
    row = transient.register(session, video_id=v.id, path=p,
                             source="apify:test", permission_basis="test",
                             now=NOW)
    assert transient.release(session, row, reason="features_persisted",
                             now=NOW) is True
    assert transient.release(session, row, reason="expired",
                             now=NOW + timedelta(hours=9)) is False
    assert row.delete_reason == "features_persisted", (
        "a second release overwrote the real reason for deletion")


def test_the_audit_answers_the_acceptance_criterion(session, quarantine):
    """M2 requires "zero competitor files older than six hours at any
    audited moment". This is that question, asked rather than assumed."""
    v = _competitor_video(session)
    transient.register(session, video_id=v.id, path=_file(quarantine),
                       source="apify:test", permission_basis="test",
                       now=NOW)
    assert transient.audit(session, now=NOW + timedelta(hours=1))[
        "guarantee_holds"] is True
    # Past the deadline and not yet swept, the guarantee is FALSE and must
    # say so rather than reporting the intention.
    stale = transient.audit(session, now=NOW + timedelta(hours=7))
    assert stale["guarantee_holds"] is False
    assert stale["overdue_rows"] == 1
    transient.sweep(session, now=NOW + timedelta(hours=7))
    assert transient.audit(session, now=NOW + timedelta(hours=7))[
        "guarantee_holds"] is True


def test_a_deletion_record_is_never_half_written(session, quarantine):
    """`deleted_at` and `delete_reason` are set together or not at all.

    Half a deletion record is worse than none, because it reads as
    audited. The constraint is in the schema so no future writer can
    produce one.
    """
    v = _competitor_video(session)
    session.add(TransientMedia(
        video_id=v.id, path=str(quarantine / "half.mp4"),
        expires_at=NOW + transient.TTL, source="apify:test",
        permission_basis="test", deleted_at=NOW))
    with pytest.raises(Exception):
        session.flush()
    session.rollback()


def test_competitor_media_status_stays_absent(session, quarantine):
    """Acquiring transient bytes must not mark the publication as having
    preserved media — it does not, and `media_status` is what the frames
    endpoint and every coverage count read."""
    v = _competitor_video(session)
    assert v.media_status == "absent"
    transient.register(session, video_id=v.id, path=_file(quarantine),
                       source="apify:test", permission_basis="test",
                       now=NOW)
    session.refresh(v)
    assert v.media_status == "absent"
    assert v.media_path is None


# ---------------------------------------------------------------------------
# acquisition
# ---------------------------------------------------------------------------

def test_own_account_media_is_never_quarantined(session, quarantine):
    """Routing an own publication through acquisition would schedule a
    file this project is supposed to keep for deletion."""
    from server.collectors import competitor_media as cm

    own = Creator(platform="tiktok", handle="me", is_own_account=True,
                  source="own_api")
    session.add(own)
    session.flush()
    v = Video(creator_id=own.id, platform="tiktok", external_id="mine",
              source="official_api")
    session.add(v)
    session.flush()
    with pytest.raises(cm.AcquisitionRefused, match="own account"):
        cm.acquire(session, videos=[v], token="x")


def test_a_publication_already_held_is_not_downloaded_again(session,
                                                            quarantine):
    """A retry must never re-download and never re-bill (CLAUDE.md §4.1).

    The ledger is the guard: a live `transient_media` row means the bytes
    are already on disk, so the provider is not asked for them twice.
    """
    from server.collectors import competitor_media as cm

    v = _competitor_video(session)
    transient.register(session, video_id=v.id, path=_file(quarantine),
                       source="apify:test", permission_basis="test", now=NOW)
    out = cm.acquire(session, videos=[v], token="x")
    assert out.skipped_already_held == 1
    assert out.acquired == 0
    assert out.billed_usd is None, "a skipped acquisition still billed"


def test_acquisition_is_refused_without_a_category_cap(session, quarantine,
                                                       monkeypatch):
    """`competitor_media` is its own category and defaults to $0.00.

    An unconfigured NEW category means "not permitted", never
    "unlimited" — so downloads cannot start just because discovery was
    once authorised.
    """
    from server import config as cfg
    from server.collectors import competitor_media as cm

    v = _competitor_video(session)
    session.flush()
    monkeypatch.setattr(
        cfg, "settings",
        lambda: cfg.Settings(raw={}, spending=cfg.Spending(
            monthly_cap_usd=5.0, providers_authorised=("apify",))))
    from server.config import SpendRefused
    with pytest.raises(SpendRefused, match="competitor_media"):
        cm.acquire(session, videos=[v], token="x")


def test_permission_basis_records_the_providers_own_retention(session,
                                                              quarantine):
    """Our six-hour deadline covers our disk, not the provider's copy.

    The actor's schema says unnamed key-value stores live 7 days. Claiming
    "no copy exists" would be false, and the stored basis is what a legal
    review reads.
    """
    from server.collectors import competitor_media as cm

    basis = cm.PERMISSION_BASIS.format(actor=cm.ACTOR, hours=6)
    assert "7 days" in basis
    assert "does not reach it" in basis


# ---------------------------------------------------------------------------
# the audit asks the disk, not only the ledger
# ---------------------------------------------------------------------------

def _age_file(path: Path, seconds: float, now: datetime = NOW) -> None:
    """Backdate a file's mtime so its real age can be exercised."""
    when = (now - timedelta(seconds=seconds)).timestamp()
    os.utime(path, (when, when))


def test_a_file_older_than_the_ttl_fails_the_audit_whatever_the_row_says(
        session, quarantine):
    """The criterion is about bytes on disk, not about a database column.

    The previous audit decided the guarantee purely from `expires_at`,
    so a row claiming a fresh deadline made a six-hour-old file
    invisible to it. The ledger is the thing that is wrong when
    something has gone wrong, so mtime is now the primary evidence.
    """
    v = _competitor_video(session)
    p = _file(quarantine)
    row = transient.register(session, video_id=v.id, path=p,
                             source="apify:test", permission_basis="test",
                             now=NOW)
    # The row insists there are hours left...
    row.expires_at = NOW + timedelta(hours=5)
    session.flush()
    # ...while the bytes have been there for seven.
    _age_file(p, seconds=7 * 3600)

    state = transient.audit(session, now=NOW)
    assert state["stale_files"] == 1
    assert state["overdue_rows"] == 0, "the row is deliberately not overdue"
    assert state["guarantee_holds"] is False
    assert any("older than" in v_ for v_ in state["violations"])


def test_the_audit_reports_the_oldest_file_not_only_the_newest(
        session, quarantine):
    """The guarantee is about the oldest file, and that number used to
    not be computed at all - `min()` of the ages was reported under the
    name `newest_file_age_s`, which is accurate and useless here."""
    v = _competitor_video(session)
    old = _file(quarantine, "competitor_1.mp4")
    new = _file(quarantine, "competitor_2.mp4")
    transient.register(session, video_id=v.id, path=old,
                       source="apify:test", permission_basis="test", now=NOW)
    transient.register(session, video_id=v.id, path=new,
                       source="apify:test", permission_basis="test", now=NOW)
    _age_file(old, seconds=3600)
    _age_file(new, seconds=60)

    state = transient.audit(session, now=NOW)
    assert state["oldest_file_age_s"] == pytest.approx(3600, abs=5)
    assert state["newest_file_age_s"] == pytest.approx(60, abs=5)


def test_an_expiry_that_is_not_acquired_at_plus_ttl_is_an_inconsistency(
        session, quarantine):
    """A deadline nobody derived from the TTL is the quiet way a file
    legitimately outlives six hours with nothing looking broken."""
    v = _competitor_video(session)
    p = _file(quarantine)
    row = transient.register(session, video_id=v.id, path=p,
                             source="apify:test", permission_basis="test",
                             now=NOW)
    row.expires_at = NOW + timedelta(days=30)
    session.flush()

    state = transient.audit(session, now=NOW)
    assert state["expiry_inconsistencies"] == 1
    assert state["ledger_consistent"] is False
    # Not a retention breach *yet* - the bytes are still young. The two
    # verdicts are deliberately separate.
    assert state["guarantee_holds"] is True


def test_a_file_older_than_the_row_that_claims_it_is_reported(
        session, quarantine):
    """A reused quarantine path: the row was written now, the bytes
    have been there far longer, so the row is describing something it
    did not see arrive."""
    v = _competitor_video(session)
    p = _file(quarantine)
    transient.register(session, video_id=v.id, path=p,
                       source="apify:test", permission_basis="test", now=NOW)
    _age_file(p, seconds=2 * 3600)

    state = transient.audit(session, now=NOW)
    assert state["rows_disagreeing_with_disk"] == 1
    assert state["ledger_consistent"] is False


def test_a_live_row_whose_file_vanished_is_reported_but_is_not_a_breach(
        session, quarantine):
    """Normal for the moment between a deletion and the next sweep. A
    count that never returns to zero means the sweep is not running."""
    v = _competitor_video(session)
    p = _file(quarantine)
    transient.register(session, video_id=v.id, path=p,
                       source="apify:test", permission_basis="test", now=NOW)
    p.unlink()

    state = transient.audit(session, now=NOW)
    assert state["rows_with_missing_file"] == 1
    assert state["ledger_consistent"] is False
    assert state["guarantee_holds"] is True


def test_a_failed_deletion_is_surfaced_by_the_audit(session, quarantine,
                                                    monkeypatch):
    """`release()` swallows an OSError on purpose, so a batch is not
    aborted mid-flight — it records the error on the row and returns
    False. Nothing else surfaced that, which meant a file that could
    not be deleted kept ageing with no sign of it anywhere.
    """
    v = _competitor_video(session)
    p = _file(quarantine)
    row = transient.register(session, video_id=v.id, path=p,
                             source="apify:test", permission_basis="test",
                             now=NOW)

    def refuse(self, *a, **k):
        raise OSError(16, "Device or resource busy")

    monkeypatch.setattr(Path, "unlink", refuse)
    assert transient.release(session, row, reason="expired", now=NOW) is False

    # The row is still live, because the file is still there.
    assert row.deleted_at is None
    assert (row.audit or {}).get("delete_error")

    state = transient.audit(session, now=NOW)
    assert state["rows_with_delete_errors"] == 1
    assert state["ledger_consistent"] is False
    assert any("delete failure" in i for i in state["inconsistencies"])


def test_a_clean_quarantine_passes_both_verdicts(session, quarantine):
    """The negative control: with nothing wrong, nothing is reported
    wrong. A checker that always finds a problem is not a checker."""
    v = _competitor_video(session)
    p = _file(quarantine)
    transient.register(session, video_id=v.id, path=p,
                       source="apify:test", permission_basis="test", now=NOW)
    state = transient.audit(session, now=NOW + timedelta(minutes=5))
    assert state["guarantee_holds"] is True
    assert state["ledger_consistent"] is True
    assert state["violations"] == []
    assert state["inconsistencies"] == []
