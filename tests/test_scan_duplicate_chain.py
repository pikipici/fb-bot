"""Phase L-1 — scan_all_sources duplicate chain absorption guard tests.

Bug discovered in obs#5 (2026-05-15):
  - Watchdog kicked scan 319 → resched chain A
  - Operator manually triggered scan 320 → resched chain B
  - Both chains co-existed for 53 minutes, finally converged at 03:35
  - Two parallel Playwright sessions on same FB account → checkpoint trigger
  - Cookie burned at T+1.27h (worse than obs#4 baseline 1.93h)

Fix: at scan_all_sources entry, check for in-flight ScannerRun (status='running'
and started recently). If found, this tick is a duplicate chain — log warning,
return early WITHOUT inserting a new ScannerRun row AND WITHOUT self-resched
(absorbing this branch).

Eligibility for "in-flight":
  - status == 'running'
  - started_at within SCAN_INFLIGHT_WINDOW_SECONDS (default 600s = 10 min,
    well above realistic scan duration ~2-3 min)

Stale 'running' rows beyond the window are ignored (worker crash leftovers
should not block forever — watchdog will eventually re-arm).
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
    monkeypatch.delenv("SCAN_SELFSCHED_DISABLED", raising=False)
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


def _seed_running_scan(
    db, *, started_offset_seconds: float = 30.0
) -> ScannerRun:
    """Insert a ScannerRun with status='running' started N seconds ago."""
    run = ScannerRun(
        task_id="inflight_task",
        trigger="selfsched",
        status="running",
        started_at=datetime.now(timezone.utc)
        - timedelta(seconds=started_offset_seconds),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _patch_common(monkeypatch, db):
    """Wire _db_session, capture self-reschedule call, capture _run_scan_all_sources."""
    monkeypatch.setattr("bot.tasks._db_session", lambda: _ctx_session(db))
    apply_async = MagicMock()
    monkeypatch.setattr(
        "bot.tasks.scan_all_sources.apply_async", apply_async
    )
    run_scan = MagicMock(
        return_value={
            "aborted": False,
            "enabled_sources": 1,
            "successful_scans": 1,
            "scan_errors": 0,
            "inserted": 0,
            "updated": 0,
            "skipped": 0,
        }
    )
    monkeypatch.setattr("bot.tasks._run_scan_all_sources", run_scan)
    return apply_async, run_scan


# ---------------------------------------------------------------------------
# Duplicate chain absorption
# ---------------------------------------------------------------------------


class TestScanDuplicateChainGuard:
    def test_inflight_running_scan_blocks_duplicate(self, monkeypatch, db):
        """Running scan exists → new tick aborts before doing any work."""
        from bot import tasks

        _seed_running_scan(db, started_offset_seconds=30)
        apply_async, run_scan = _patch_common(monkeypatch, db)

        result = tasks.scan_all_sources(trigger="selfsched")

        assert result["aborted"] is True
        assert result["reason"] == "duplicate_chain"
        # Critical: no new ScannerRun row inserted.
        rows = db.query(ScannerRun).all()
        assert len(rows) == 1
        assert rows[0].status == "running"  # Original untouched.
        # Critical: no self-reschedule (would multiply chain).
        apply_async.assert_not_called()
        # Body never executed.
        run_scan.assert_not_called()

    def test_no_inflight_runs_normally(self, monkeypatch, db):
        """No running scan → tick proceeds + self-reschedules."""
        from bot import tasks

        apply_async, run_scan = _patch_common(monkeypatch, db)

        result = tasks.scan_all_sources(trigger="selfsched")

        assert result.get("aborted", False) is False
        rows = db.query(ScannerRun).all()
        assert len(rows) == 1
        assert rows[0].status == "success"
        # Self-resched fires happy-path.
        apply_async.assert_called_once()
        run_scan.assert_called_once()

    def test_stale_running_row_ignored_beyond_window(self, monkeypatch, db):
        """ScannerRun stuck 'running' for 30 min (worker crash) is ignored.

        Otherwise a leftover row would block the chain forever.
        """
        from bot import tasks

        # 30 min ago — way past 10-min window.
        _seed_running_scan(db, started_offset_seconds=30 * 60)
        apply_async, run_scan = _patch_common(monkeypatch, db)

        result = tasks.scan_all_sources(trigger="selfsched")

        assert result.get("aborted", False) is False
        # New row created (stale one ignored).
        rows = db.query(ScannerRun).all()
        assert len(rows) == 2
        # Self-resched fires.
        apply_async.assert_called_once()
        run_scan.assert_called_once()

    def test_just_finished_scan_does_not_block(self, monkeypatch, db):
        """ScannerRun with status='success' (just finished) does NOT block."""
        from bot import tasks

        finished = ScannerRun(
            task_id="just_done",
            trigger="selfsched",
            status="success",
            started_at=datetime.now(timezone.utc) - timedelta(seconds=120),
            finished_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        )
        db.add(finished)
        db.commit()
        apply_async, run_scan = _patch_common(monkeypatch, db)

        result = tasks.scan_all_sources(trigger="selfsched")

        assert result.get("aborted", False) is False
        run_scan.assert_called_once()
        apply_async.assert_called_once()

    def test_window_boundary_via_env_override(self, monkeypatch, db):
        """SCAN_INFLIGHT_WINDOW_SECONDS env knob tunable for ops."""
        from bot import tasks

        monkeypatch.setenv("SCAN_INFLIGHT_WINDOW_SECONDS", "1200")  # 20 min
        # Running scan started 15 min ago — still inside the wider window.
        _seed_running_scan(db, started_offset_seconds=15 * 60)
        apply_async, run_scan = _patch_common(monkeypatch, db)

        result = tasks.scan_all_sources(trigger="selfsched")

        assert result["aborted"] is True
        assert result["reason"] == "duplicate_chain"
        run_scan.assert_not_called()
        apply_async.assert_not_called()

    def test_manual_trigger_also_absorbed_when_inflight(self, monkeypatch, db):
        """Even trigger='manual' bows to in-flight scan (prevents lu's bug).

        This is the exact scenario that broke obs#5: operator manually
        triggered while watchdog had just kicked the chain — both ran in
        parallel.
        """
        from bot import tasks

        _seed_running_scan(db, started_offset_seconds=15)
        apply_async, run_scan = _patch_common(monkeypatch, db)

        result = tasks.scan_all_sources(trigger="manual")

        assert result["aborted"] is True
        assert result["reason"] == "duplicate_chain"
        run_scan.assert_not_called()
        apply_async.assert_not_called()

    def test_watchdog_trigger_also_absorbed_when_inflight(
        self, monkeypatch, db
    ):
        """Watchdog kick that races with running scan also bows out."""
        from bot import tasks

        _seed_running_scan(db, started_offset_seconds=15)
        apply_async, run_scan = _patch_common(monkeypatch, db)

        result = tasks.scan_all_sources(trigger="watchdog")

        assert result["aborted"] is True
        assert result["reason"] == "duplicate_chain"
        run_scan.assert_not_called()
        apply_async.assert_not_called()
