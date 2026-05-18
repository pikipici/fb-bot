"""Phase M-1 Part 2 — SoftTimeLimitExceeded handler.

When Celery raises ``SoftTimeLimitExceeded`` mid-task (12 min for scan,
8 min for auto_comment), the task should:
  * Catch it explicitly (NOT lump with generic Exception)
  * Finalize ``ScannerRun`` / ``CommentHistory`` with
    ``aborted_reason='soft_time_limit'`` so dashboard ops can spot it
  * Reschedule the chain so the next tick fires (mirror crash branch)

Hard ``time_limit`` (15 / 10 min) hits ~3 / ~2 min later → SIGKILL
worker if it didn't unwind cleanly. ``Restart=always`` brings it back.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.database import Base
from server.models import FBAccount, ScannerRun, Source


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


def _seed_account_with_source(db):
    from server.crypto import encrypt_cookies

    cookies = {"c_user": "61577777450562", "xs": "abc"}
    account = FBAccount(
        label="A",
        status="ACTIVE",
        fb_user_id="61577777450562",
        fb_name="Test",
        cookies_encrypted=encrypt_cookies(cookies),
    )
    db.add(account)
    src = Source(type="home_feed", label="Beranda", enabled=True)
    db.add(src)
    db.commit()
    return account


class TestScanAllSourcesSoftTimeLimit:
    """Phase M-1 — scan_all_sources catches SoftTimeLimitExceeded."""

    def test_soft_timeout_finalizes_with_soft_time_limit_reason(
        self, db, monkeypatch
    ):
        from bot import tasks

        _seed_account_with_source(db)
        monkeypatch.setattr(tasks, "_db_session", lambda: _ctx_session(db))

        # Make the inner pipeline raise SoftTimeLimitExceeded mid-flight.
        def _boom(_db):
            raise SoftTimeLimitExceeded()

        monkeypatch.setattr(tasks, "_run_scan_all_sources", _boom)

        # Reschedule shouldn't crash the test even if it would normally
        # try to call apply_async.
        monkeypatch.setattr(tasks, "_enqueue_next_scan", lambda: None)

        result = tasks.scan_all_sources.run(trigger="manual")

        assert result["aborted"] is True
        assert result["reason"] == "soft_time_limit"

        run = db.query(ScannerRun).order_by(ScannerRun.id.desc()).first()
        assert run is not None
        assert run.status == "failed"
        assert run.aborted_reason == "soft_time_limit"
        assert run.finished_at is not None

    def test_soft_timeout_still_reschedules_chain(self, db, monkeypatch):
        """Chain alive even after soft timeout — same as crash branch."""
        from bot import tasks

        _seed_account_with_source(db)
        monkeypatch.setattr(tasks, "_db_session", lambda: _ctx_session(db))

        def _boom(_db):
            raise SoftTimeLimitExceeded()

        monkeypatch.setattr(tasks, "_run_scan_all_sources", _boom)

        resched = MagicMock()
        monkeypatch.setattr(tasks, "_enqueue_next_scan", resched)

        tasks.scan_all_sources.run(trigger="manual")

        resched.assert_called_once()


class TestAutoCommentNextSoftTimeLimit:
    """Phase M-1 — auto_comment_next catches SoftTimeLimitExceeded."""

    def test_soft_timeout_records_failure_and_reschedules(
        self, db, monkeypatch
    ):
        from bot import tasks

        # Bypass fire-lock so the task body actually runs.
        monkeypatch.setattr(
            tasks, "_acquire_auto_comment_fire_lock", lambda: True
        )
        monkeypatch.setattr(tasks, "_db_session", lambda: _ctx_session(db))

        # Make eligible-post pick raise SoftTimeLimitExceeded as if the
        # task got killed mid-pipeline.
        with patch(
            "server.services.auto_comment_service.AutoCommentService"
        ) as mock_svc:
            instance = MagicMock()
            instance.pick_next_eligible_post.side_effect = (
                SoftTimeLimitExceeded()
            )
            mock_svc.return_value = instance

            resched = MagicMock()
            monkeypatch.setattr(tasks, "_enqueue_next_comment", resched)

            result = tasks.auto_comment_next.run(trigger="selfsched")

        assert result["action"] == "soft_timeout"
        assert result["reason"] == "soft_time_limit"
        resched.assert_called_once()
