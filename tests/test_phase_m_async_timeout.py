"""Phase M-2 — asyncio outer timeout wrap around Playwright operations.

Background: a wedged Playwright pipe (FB anti-bot, Chromium freeze, broken
network) keeps ``asyncio.run()`` blocked in ``select()`` forever. Phase L-2
watchdog flips the DB row but the worker process stays nyangkut, eating
both worker slots until time_limit (M-1) hits.

M-2 wraps the Playwright coroutine in ``asyncio.wait_for(... timeout=N)``
so the loop unblocks gracefully (``asyncio.TimeoutError`` propagates,
finalize logic runs, chain reschedules). M-1's hard time_limit is the
last-resort safety net; M-2 is the preferred graceful path.

Defaults:
  SCAN_OUTER_TIMEOUT_SECONDS=720      (12 min)
  COMMENT_OUTER_TIMEOUT_SECONDS=420   (7 min)

These sit just under M-1 ``soft_time_limit`` (scan 720s, comment 480s)
so the inner timeout fires first and the task can finalize before
SoftTimeLimitExceeded.
"""
from __future__ import annotations

import asyncio
import importlib

import pytest
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


class TestScanOuterTimeoutKnob:
    """Phase M-2 — env-driven outer timeout for scan."""

    def test_default_is_720_seconds(self, monkeypatch):
        monkeypatch.delenv("SCAN_OUTER_TIMEOUT_SECONDS", raising=False)
        import bot.celery_app as mod

        importlib.reload(mod)
        assert mod._scan_outer_timeout() == 720

    def test_env_override_respected(self, monkeypatch):
        monkeypatch.setenv("SCAN_OUTER_TIMEOUT_SECONDS", "300")
        import bot.celery_app as mod

        importlib.reload(mod)
        assert mod._scan_outer_timeout() == 300


class TestCommentOuterTimeoutKnob:
    """Phase M-2 — env-driven outer timeout for auto_comment."""

    def test_default_is_420_seconds(self, monkeypatch):
        monkeypatch.delenv("COMMENT_OUTER_TIMEOUT_SECONDS", raising=False)
        import bot.celery_app as mod

        importlib.reload(mod)
        assert mod._comment_outer_timeout() == 420

    def test_env_override_respected(self, monkeypatch):
        monkeypatch.setenv("COMMENT_OUTER_TIMEOUT_SECONDS", "180")
        import bot.celery_app as mod

        importlib.reload(mod)
        assert mod._comment_outer_timeout() == 180


class TestScanAsyncioTimeout:
    """Phase M-2 — scan_all_sources catches asyncio.TimeoutError."""

    def test_async_timeout_finalizes_with_asyncio_timeout_reason(
        self, db, monkeypatch
    ):
        from bot import tasks

        _seed_account_with_source(db)
        monkeypatch.setattr(tasks, "_db_session", lambda: _ctx_session(db))

        # Force the inner pipeline to surface asyncio.TimeoutError as if
        # asyncio.wait_for fired. The exact mechanism inside
        # _run_scan_all_sources is M-2 implementation detail; this test
        # asserts the OUTER task contract — TimeoutError caught,
        # ScannerRun finalized 'failed' with aborted_reason, chain
        # rescheduled.
        def _boom(_db):
            raise asyncio.TimeoutError("scan outer timeout")

        monkeypatch.setattr(tasks, "_run_scan_all_sources", _boom)

        resched_calls = []
        monkeypatch.setattr(
            tasks,
            "_enqueue_next_scan",
            lambda: resched_calls.append("called"),
        )

        result = tasks.scan_all_sources.run(trigger="manual")

        assert result["aborted"] is True
        assert result["reason"] == "asyncio_timeout"

        run = db.query(ScannerRun).order_by(ScannerRun.id.desc()).first()
        assert run is not None
        assert run.status == "failed"
        assert run.aborted_reason == "asyncio_timeout"
        assert resched_calls == ["called"]


class TestCommentAsyncioTimeout:
    """Phase M-2 — auto_comment_next catches asyncio.TimeoutError."""

    def test_async_timeout_returns_asyncio_timeout_action(
        self, db, monkeypatch
    ):
        from unittest.mock import MagicMock, patch

        from bot import tasks

        monkeypatch.setattr(
            tasks, "_acquire_auto_comment_fire_lock", lambda: True
        )
        monkeypatch.setattr(tasks, "_db_session", lambda: _ctx_session(db))

        with patch(
            "server.services.auto_comment_service.AutoCommentService"
        ) as mock_svc:
            instance = MagicMock()
            instance.pick_next_eligible_post.side_effect = (
                asyncio.TimeoutError("comment outer timeout")
            )
            mock_svc.return_value = instance

            resched = MagicMock()
            monkeypatch.setattr(tasks, "_enqueue_next_comment", resched)

            result = tasks.auto_comment_next.run(trigger="selfsched")

        assert result["action"] == "async_timeout"
        assert result["reason"] == "asyncio_timeout"
        resched.assert_called_once()
