"""Phase L-2 — scan_watchdog stale 'running' row reaper tests.

Bug observed in obs#6 (2026-05-15 13:30 UTC):
  - scan 375 started, hit `Block detected: checkpoint` mid-run
  - Worker crashed without _finalize_scanner_run flipping status
  - Row stuck status='running' indefinitely
  - watchdog skip='running' = chain frozen
  - Phase L-1 guard sees stale row + cutoff window expired but doesn't reap

Two parallel chains existed for ~2h (375 zombie + 376-380 fresh). Cookie
miraculously survived but auto_comment chain exploded due to other bug.

Fix: scan_watchdog reaps any row with status='running' AND
  started_at < now() - SCAN_RUNNING_TIMEOUT_SECONDS (default 600s = 10 min,
  generous vs realistic scan duration ~2-3 min). Marks failed +
  aborted_reason='watchdog_zombie_reap', then continues normal logic
  (kicks fresh scan if chain truly stale).

Why generous default? FB scans can run 2-5 min normally. Reap should never
race a healthy scan. 10 min = ~3x worst case headroom.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.database import Base
from server.models import ScannerRun


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "CREDENTIALS_KEY", "WyzJqG3Vg9ZpUyFkq4bUxN9yxMG3xCyq4Rr8s3fL7dE="
    )
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _ctx_session(session):
    from contextlib import contextmanager

    @contextmanager
    def _cm():
        yield session

    return _cm()


def _seed_running(
    db,
    *,
    started_offset_seconds: float,
    trigger: str = "selfsched",
) -> ScannerRun:
    run = ScannerRun(
        task_id=f"task_{started_offset_seconds}",
        trigger=trigger,
        status="running",
        started_at=datetime.now(timezone.utc)
        - timedelta(seconds=started_offset_seconds),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _patch_watchdog(monkeypatch, db):
    monkeypatch.setattr("bot.tasks._db_session", lambda: _ctx_session(db))
    apply_async = MagicMock()
    monkeypatch.setattr(
        "bot.tasks.scan_all_sources.apply_async", apply_async
    )
    return apply_async


# ---------------------------------------------------------------------------
# Stale running row reaper
# ---------------------------------------------------------------------------


class TestScanWatchdogZombieReap:
    def test_zombie_running_row_reaped_after_timeout(self, monkeypatch, db):
        """Row 'running' for 30 min → reaped to 'failed' + watchdog continues."""
        from bot import tasks

        # 30 min ago — way past 10-min default timeout.
        zombie = _seed_running(db, started_offset_seconds=30 * 60)
        apply_async = _patch_watchdog(monkeypatch, db)

        result = tasks.scan_watchdog()

        # Zombie row reaped.
        db.refresh(zombie)
        assert zombie.status == "failed"
        assert zombie.aborted_reason == "watchdog_zombie_reap"
        assert zombie.finished_at is not None
        # Watchdog should kick fresh scan since chain is now stale.
        apply_async.assert_called_once()
        assert result["action"] == "kick"

    def test_fresh_running_row_not_reaped(self, monkeypatch, db):
        """Row 'running' for 60s → still healthy, watchdog skips."""
        from bot import tasks

        fresh = _seed_running(db, started_offset_seconds=60)
        apply_async = _patch_watchdog(monkeypatch, db)

        result = tasks.scan_watchdog()

        db.refresh(fresh)
        assert fresh.status == "running"  # untouched
        apply_async.assert_not_called()
        assert result["action"] == "skip"
        assert result["reason"] == "running"

    def test_multiple_zombies_all_reaped(self, monkeypatch, db):
        """Multiple stale running rows → all flipped failed in one tick."""
        from bot import tasks

        z1 = _seed_running(db, started_offset_seconds=20 * 60)
        z2 = _seed_running(db, started_offset_seconds=45 * 60)
        z3 = _seed_running(db, started_offset_seconds=15 * 60)
        _patch_watchdog(monkeypatch, db)

        tasks.scan_watchdog()

        for zombie in (z1, z2, z3):
            db.refresh(zombie)
            assert zombie.status == "failed"
            assert zombie.aborted_reason == "watchdog_zombie_reap"

    def test_running_timeout_env_override(self, monkeypatch, db):
        """SCAN_RUNNING_TIMEOUT_SECONDS env knob tunes reap threshold."""
        from bot import tasks

        # Tighter 5-min timeout — row 8 min old should reap.
        monkeypatch.setenv("SCAN_RUNNING_TIMEOUT_SECONDS", "300")
        zombie = _seed_running(db, started_offset_seconds=8 * 60)
        _patch_watchdog(monkeypatch, db)

        tasks.scan_watchdog()

        db.refresh(zombie)
        assert zombie.status == "failed"

    def test_running_timeout_env_keeps_fresh_alive(self, monkeypatch, db):
        """Wider timeout via env doesn't reap a row inside the new window."""
        from bot import tasks

        # Wider 30-min timeout — row 15 min old should stay running.
        monkeypatch.setenv("SCAN_RUNNING_TIMEOUT_SECONDS", "1800")
        fresh = _seed_running(db, started_offset_seconds=15 * 60)
        _patch_watchdog(monkeypatch, db)

        tasks.scan_watchdog()

        db.refresh(fresh)
        assert fresh.status == "running"

    def test_zombie_reap_does_not_block_chain_continuation(
        self, monkeypatch, db
    ):
        """After reap, watchdog kicks fresh scan (chain alive again)."""
        from bot import tasks

        # Zombie + already-finished run before it.
        finished = ScannerRun(
            task_id="old",
            trigger="selfsched",
            status="success",
            started_at=datetime.now(timezone.utc) - timedelta(hours=2),
            finished_at=datetime.now(timezone.utc) - timedelta(hours=1, minutes=58),
        )
        db.add(finished)
        zombie = _seed_running(db, started_offset_seconds=60 * 60)
        db.commit()
        apply_async = _patch_watchdog(monkeypatch, db)

        result = tasks.scan_watchdog()

        db.refresh(zombie)
        assert zombie.status == "failed"
        # Watchdog reported chain as stale → kicked.
        apply_async.assert_called_once()
        assert result["action"] == "kick"
