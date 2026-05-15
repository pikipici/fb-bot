"""Phase K-4 — auto_comment_watchdog stale-chain detection.

Mirror of scan_watchdog (Phase J-3) but for the comment chain.

Logic:
  - If kill-switch (AUTO_COMMENT_DISABLED=1) → noop, no kick.
  - If no CommentHistory rows ever → kick (bootstrap).
  - If last row sent_at within AUTO_COMMENT_MAX_IDLE_SECONDS → skip (healthy).
  - Else → kick (stale).

Watchdog NEVER raises — defensive against DB hiccups. Returns
``{action, reason, [idle_seconds]}`` for observability.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.database import Base
from server.models import CommentHistory, Source, TrendingPost


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "CREDENTIALS_KEY", "WyzJqG3Vg9ZpUyFkq4bUxN9yxMG3xCyq4Rr8s3fL7dE="
    )
    monkeypatch.delenv("AUTO_COMMENT_DISABLED", raising=False)
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


def _seed_post(db) -> TrendingPost:
    src = Source(type="home_feed", label="beranda", enabled=True)
    db.add(src)
    db.commit()
    db.refresh(src)
    post = TrendingPost(
        fb_post_id="p1",
        source_id=src.id,
        post_url="https://www.facebook.com/test/p1",
        status="COMMENTED",
        collected_at=datetime.now(timezone.utc),
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    return post


def _seed_history(db, *, status: str, sent_minutes_ago: float) -> CommentHistory:
    post = _seed_post(db)
    sent = datetime.now(timezone.utc) - timedelta(minutes=sent_minutes_ago)
    row = CommentHistory(
        trending_post_id=post.id,
        comment_text="dummy",
        status=status,
        sent_at=sent,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


class TestAutoCommentWatchdog:
    def test_no_history_kicks_bootstrap(self, monkeypatch, db):
        from bot import tasks

        monkeypatch.setattr(
            "bot.tasks._db_session", lambda: _ctx_session(db)
        )
        apply_async = MagicMock()
        monkeypatch.setattr(
            "bot.tasks.auto_comment_next.apply_async", apply_async
        )

        result = tasks.auto_comment_watchdog()

        assert result["action"] == "kick"
        assert result["reason"] == "no_history"
        apply_async.assert_called_once()
        kwargs = apply_async.call_args.kwargs
        assert kwargs["kwargs"] == {"trigger": "watchdog"}

    def test_recent_sent_skips(self, monkeypatch, db):
        from bot import tasks

        _seed_history(db, status="SENT", sent_minutes_ago=5.0)

        monkeypatch.setattr(
            "bot.tasks._db_session", lambda: _ctx_session(db)
        )
        apply_async = MagicMock()
        monkeypatch.setattr(
            "bot.tasks.auto_comment_next.apply_async", apply_async
        )

        result = tasks.auto_comment_watchdog()

        assert result["action"] == "skip"
        assert result["reason"] == "fresh"
        apply_async.assert_not_called()

    def test_recent_failed_also_skips(self, monkeypatch, db):
        """FAILED rows also count as 'chain alive' — pipeline ran, just errored."""
        from bot import tasks

        _seed_history(db, status="FAILED", sent_minutes_ago=5.0)

        monkeypatch.setattr(
            "bot.tasks._db_session", lambda: _ctx_session(db)
        )
        apply_async = MagicMock()
        monkeypatch.setattr(
            "bot.tasks.auto_comment_next.apply_async", apply_async
        )

        result = tasks.auto_comment_watchdog()

        assert result["action"] == "skip"
        assert result["reason"] == "fresh"
        apply_async.assert_not_called()

    def test_stale_history_kicks(self, monkeypatch, db):
        from bot import tasks

        _seed_history(db, status="SENT", sent_minutes_ago=45.0)

        monkeypatch.setattr(
            "bot.tasks._db_session", lambda: _ctx_session(db)
        )
        apply_async = MagicMock()
        monkeypatch.setattr(
            "bot.tasks.auto_comment_next.apply_async", apply_async
        )

        result = tasks.auto_comment_watchdog()

        assert result["action"] == "kick"
        assert result["reason"] == "stale"
        assert "idle_seconds" in result
        apply_async.assert_called_once()

    def test_env_threshold_override(self, monkeypatch, db):
        from bot import tasks

        # 12 min ago — fresh by default (1800s), stale at 600s.
        _seed_history(db, status="SENT", sent_minutes_ago=12.0)
        monkeypatch.setenv("AUTO_COMMENT_MAX_IDLE_SECONDS", "600")

        monkeypatch.setattr(
            "bot.tasks._db_session", lambda: _ctx_session(db)
        )
        apply_async = MagicMock()
        monkeypatch.setattr(
            "bot.tasks.auto_comment_next.apply_async", apply_async
        )

        result = tasks.auto_comment_watchdog()

        assert result["action"] == "kick"
        assert result["reason"] == "stale"
        apply_async.assert_called_once()

    def test_kill_switch_skips(self, monkeypatch, db):
        from bot import tasks

        monkeypatch.setenv("AUTO_COMMENT_DISABLED", "1")
        monkeypatch.setattr(
            "bot.tasks._db_session", lambda: _ctx_session(db)
        )
        apply_async = MagicMock()
        monkeypatch.setattr(
            "bot.tasks.auto_comment_next.apply_async", apply_async
        )

        result = tasks.auto_comment_watchdog()

        assert result["action"] == "skip"
        assert result["reason"] == "kill_switch"
        apply_async.assert_not_called()

    def test_naive_sent_at_does_not_crash(self, monkeypatch, db):
        from bot import tasks

        post = _seed_post(db)
        # Naive datetime (legacy row).
        row = CommentHistory(
            trending_post_id=post.id,
            comment_text="legacy",
            status="SENT",
            sent_at=datetime.utcnow() - timedelta(minutes=45),
        )
        db.add(row)
        db.commit()

        monkeypatch.setattr(
            "bot.tasks._db_session", lambda: _ctx_session(db)
        )
        apply_async = MagicMock()
        monkeypatch.setattr(
            "bot.tasks.auto_comment_next.apply_async", apply_async
        )

        result = tasks.auto_comment_watchdog()

        # Should not crash on tz-aware/naive comparison.
        assert result["action"] in ("kick", "skip")
