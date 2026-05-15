"""Tests for ``bot.tasks.scan_watchdog`` (Phase J-3).

Watchdog runs every 5 minutes via beat schedule. It picks up the trail
when the self-rescheduling chain breaks (worker crash mid-task, broker
hiccup, fresh deploy with empty queue).

Logic:
  - If a ScannerRun with status='running' exists → skip (scan in flight)
  - Else if last ScannerRun is None → kick (bootstrap)
  - Else if last_finished < SCAN_MAX_IDLE_SECONDS ago → skip (chain healthy)
  - Else → kick (stale)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.models import Base, ScannerRun


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
    """Mimic ``_db_session`` contextmanager but yield the test session."""
    from contextlib import contextmanager

    @contextmanager
    def _cm():
        yield session

    return _cm()


def _seed_run(
    db,
    *,
    status: str,
    finished_minutes_ago: float | None,
    started_minutes_ago: float = 60.0,
):
    """Seed a ScannerRun row with synthetic timestamps."""
    now = datetime.now(timezone.utc)
    started = now - timedelta(minutes=started_minutes_ago)
    finished = (
        None
        if finished_minutes_ago is None
        else now - timedelta(minutes=finished_minutes_ago)
    )
    row = ScannerRun(
        task_id=f"test-{status}-{started_minutes_ago}",
        trigger="selfsched",
        status=status,
        started_at=started,
        finished_at=finished,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


class TestScanWatchdog:
    """Phase J-3 — ``scan_watchdog`` safety-net behavior."""

    def test_no_history_kicks_bootstrap_scan(self, monkeypatch, db):
        """Empty ScannerRun table — watchdog kicks first scan."""
        from bot import tasks

        monkeypatch.setattr(
            "bot.tasks._db_session", lambda: _ctx_session(db)
        )
        apply_async = MagicMock()
        monkeypatch.setattr(
            "bot.tasks.scan_all_sources.apply_async", apply_async
        )

        result = tasks.scan_watchdog()

        assert result["action"] == "kick"
        assert result["reason"] == "no_history"
        apply_async.assert_called_once()
        # Bootstrap kick uses watchdog trigger, no countdown delay.
        kwargs = apply_async.call_args.kwargs
        assert kwargs["kwargs"] == {"trigger": "watchdog"}

    def test_running_scan_skips_kick(self, monkeypatch, db):
        """Active scan in flight — watchdog defers, no double-trigger."""
        from bot import tasks

        _seed_run(
            db,
            status="running",
            finished_minutes_ago=None,
            started_minutes_ago=2.0,
        )

        monkeypatch.setattr(
            "bot.tasks._db_session", lambda: _ctx_session(db)
        )
        apply_async = MagicMock()
        monkeypatch.setattr(
            "bot.tasks.scan_all_sources.apply_async", apply_async
        )

        result = tasks.scan_watchdog()

        assert result["action"] == "skip"
        assert result["reason"] == "running"
        apply_async.assert_not_called()

    def test_fresh_last_run_skips_kick(self, monkeypatch, db):
        """Recently-finished scan (within SCAN_MAX_IDLE) — chain healthy."""
        from bot import tasks

        _seed_run(
            db,
            status="success",
            finished_minutes_ago=5.0,
            started_minutes_ago=10.0,
        )

        monkeypatch.setattr(
            "bot.tasks._db_session", lambda: _ctx_session(db)
        )
        apply_async = MagicMock()
        monkeypatch.setattr(
            "bot.tasks.scan_all_sources.apply_async", apply_async
        )

        result = tasks.scan_watchdog()

        assert result["action"] == "skip"
        assert result["reason"] == "fresh"
        apply_async.assert_not_called()

    def test_stale_last_run_kicks_kick(self, monkeypatch, db):
        """Last scan finished long ago (> SCAN_MAX_IDLE) — chain broken."""
        from bot import tasks

        _seed_run(
            db,
            status="success",
            finished_minutes_ago=45.0,
            started_minutes_ago=50.0,
        )

        monkeypatch.setattr(
            "bot.tasks._db_session", lambda: _ctx_session(db)
        )
        apply_async = MagicMock()
        monkeypatch.setattr(
            "bot.tasks.scan_all_sources.apply_async", apply_async
        )

        result = tasks.scan_watchdog()

        assert result["action"] == "kick"
        assert result["reason"] == "stale"
        assert "idle_seconds" in result
        apply_async.assert_called_once()

    def test_stale_threshold_respects_env(self, monkeypatch, db):
        """``SCAN_MAX_IDLE_SECONDS`` env knob shifts the freshness window."""
        from bot import tasks

        # Last finished 12 min ago → fresh by default (1800s = 30 min),
        # but stale if we set the threshold to 10 min.
        _seed_run(
            db,
            status="success",
            finished_minutes_ago=12.0,
            started_minutes_ago=15.0,
        )
        monkeypatch.setenv("SCAN_MAX_IDLE_SECONDS", "600")  # 10 min

        monkeypatch.setattr(
            "bot.tasks._db_session", lambda: _ctx_session(db)
        )
        apply_async = MagicMock()
        monkeypatch.setattr(
            "bot.tasks.scan_all_sources.apply_async", apply_async
        )

        result = tasks.scan_watchdog()

        assert result["action"] == "kick"
        assert result["reason"] == "stale"
        apply_async.assert_called_once()

    def test_naive_finished_at_is_handled(self, monkeypatch, db):
        """Old DB rows with naive datetime should not crash watchdog."""
        from bot import tasks

        # Manually insert a row with a naive datetime. Some legacy rows
        # were saved without explicit tz before Phase F7 fixes.
        now = datetime.utcnow()
        row = ScannerRun(
            task_id="legacy-naive",
            trigger="beat",
            status="success",
            started_at=now - timedelta(minutes=60),
            finished_at=now - timedelta(minutes=45),
        )
        db.add(row)
        db.commit()

        monkeypatch.setattr(
            "bot.tasks._db_session", lambda: _ctx_session(db)
        )
        apply_async = MagicMock()
        monkeypatch.setattr(
            "bot.tasks.scan_all_sources.apply_async", apply_async
        )

        # Should not raise TypeError on tz-aware/naive comparison.
        result = tasks.scan_watchdog()
        assert result["action"] in ("kick", "skip")

    def test_falls_back_to_started_at_when_finished_missing(
        self, monkeypatch, db
    ):
        """If finished_at is NULL (e.g. mid-flight scan crashed mid-row),
        use started_at as the freshness signal."""
        from bot import tasks

        # A row that started 50 min ago, never finished (e.g. worker died).
        # Should be considered stale (started_at well past 30-min default).
        _seed_run(
            db,
            status="failed",
            finished_minutes_ago=None,
            started_minutes_ago=50.0,
        )

        monkeypatch.setattr(
            "bot.tasks._db_session", lambda: _ctx_session(db)
        )
        apply_async = MagicMock()
        monkeypatch.setattr(
            "bot.tasks.scan_all_sources.apply_async", apply_async
        )

        result = tasks.scan_watchdog()

        assert result["action"] == "kick"
        assert result["reason"] == "stale"
        apply_async.assert_called_once()
