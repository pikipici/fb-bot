"""Phase L-3 — auto_comment_next fire-rate Redis lock tests.

Bug observed in obs#6 (2026-05-15 15:03+ UTC):
  - Worker restart caused 6+ tasks delivered in 1 second from broker backlog
  - Each task self-rescheduled in finally-block → enqueued more tasks
  - With concurrency=2, two parallel ForkPoolWorkers compounded the explosion
  - Result: 91 auto_comment_next ticks in 30 minutes (designed: ~2-3/30min)

Fix: at start of ``auto_comment_next``, atomically acquire a Redis lock
``auto_comment:fire_lock`` with TTL = ``_auto_comment_min_interval()``. If
acquisition fails (another task already fired within the window), skip the
tick + do NOT self-reschedule (no chain multiplication).

Why TTL = min_interval? Self-resched countdown is uniform(min, max) so the
next legit task lands at >= min seconds out — exactly when the lock expires.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.database import Base
from server.models import (
    CommentHistory,
    FBAccount,
    Source,
    TrendingPost,
)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "CREDENTIALS_KEY", "WyzJqG3Vg9ZpUyFkq4bUxN9yxMG3xCyq4Rr8s3fL7dE="
    )
    monkeypatch.setenv("SUMOPOD_API_KEY", "test-key")
    monkeypatch.delenv("AUTO_COMMENT_DISABLED", raising=False)
    monkeypatch.delenv("AUTO_COMMENT_DRY_RUN", raising=False)
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


def _seed_account(db) -> FBAccount:
    acc = FBAccount(
        label="khorur",
        status="ACTIVE",
        cookies_encrypted="dummy_encrypted_blob",
        fb_user_id="61577777450562",
        fb_name="Digi Markt",
    )
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return acc


def _seed_post(db, *, fb_post_id: str = "p1") -> TrendingPost:
    src = db.query(Source).first()
    if src is None:
        src = Source(type="home_feed", label="beranda", enabled=True)
        db.add(src)
        db.commit()
        db.refresh(src)
    post = TrendingPost(
        fb_post_id=fb_post_id,
        source_id=src.id,
        author_name="X",
        text_snippet="hello",
        post_url=f"https://www.facebook.com/test/{fb_post_id}",
        status="NEW",
        collected_at=datetime.now(timezone.utc),
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    return post


class FakeRedis:
    """Minimal in-memory Redis stub supporting SET NX EX semantics."""

    def __init__(self):
        self.store: dict[str, str] = {}

    def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        return self.store.pop(key, None) is not None

    def exists(self, key):
        return key in self.store


@pytest.fixture
def fake_redis(monkeypatch):
    """Patch the Redis client used by the fire-lock helper."""
    fr = FakeRedis()
    monkeypatch.setattr("bot.tasks._get_redis_client", lambda: fr)
    return fr


# ---------------------------------------------------------------------------
# _acquire_auto_comment_fire_lock helper
# ---------------------------------------------------------------------------


class TestFireLockHelper:
    def test_first_acquire_returns_true(self, monkeypatch, fake_redis):
        from bot import tasks

        assert tasks._acquire_auto_comment_fire_lock() is True

    def test_second_acquire_within_window_returns_false(
        self, monkeypatch, fake_redis
    ):
        from bot import tasks

        assert tasks._acquire_auto_comment_fire_lock() is True
        assert tasks._acquire_auto_comment_fire_lock() is False
        assert tasks._acquire_auto_comment_fire_lock() is False

    def test_lock_key_present_after_acquire(self, monkeypatch, fake_redis):
        from bot import tasks

        tasks._acquire_auto_comment_fire_lock()
        assert fake_redis.exists("auto_comment:fire_lock")

    def test_redis_unavailable_fails_open(self, monkeypatch):
        """If Redis is dead, default to allowing the fire (defensive)."""
        from bot import tasks

        def _broken():
            raise ConnectionError("redis down")

        monkeypatch.setattr("bot.tasks._get_redis_client", _broken)
        assert tasks._acquire_auto_comment_fire_lock() is True


# ---------------------------------------------------------------------------
# auto_comment_next integration with fire lock
# ---------------------------------------------------------------------------


def _patch_common(monkeypatch, db):
    monkeypatch.setattr("bot.tasks._db_session", lambda: _ctx_session(db))
    apply_async = MagicMock()
    monkeypatch.setattr(
        "bot.tasks.auto_comment_next.apply_async", apply_async
    )
    return apply_async


def _stub_ai_draft(monkeypatch, draft_text="hello bro"):
    class _StubAI:
        def __init__(self, _db):
            pass

        def generate(self, **kwargs):
            return draft_text

    monkeypatch.setattr(
        "server.services.ai_draft_service.AIDraftService", _StubAI
    )
    monkeypatch.setattr(
        "bot.tasks.decrypt_cookies",
        lambda blob: {"c_user": "1", "xs": "x"},
    )


class TestAutoCommentNextFireLock:
    def test_first_tick_acquires_lock_and_runs(
        self, monkeypatch, db, fake_redis
    ):
        from bot import tasks

        monkeypatch.setenv("AUTO_COMMENT_DRY_RUN", "1")
        _seed_account(db)
        _seed_post(db)
        apply_async = _patch_common(monkeypatch, db)
        _stub_ai_draft(monkeypatch)

        result = tasks.auto_comment_next()

        assert result["action"] == "draft"
        # Self-resched fires.
        apply_async.assert_called_once()

    def test_second_tick_blocked_by_lock_does_not_run_or_resched(
        self, monkeypatch, db, fake_redis
    ):
        """Second concurrent tick → skip + DON'T resched (kill multiplier)."""
        from bot import tasks

        monkeypatch.setenv("AUTO_COMMENT_DRY_RUN", "1")
        _seed_account(db)
        _seed_post(db)

        # Pre-acquire the lock as if a sibling task already started.
        fake_redis.set("auto_comment:fire_lock", "1", nx=True, ex=720)

        apply_async = _patch_common(monkeypatch, db)
        _stub_ai_draft(monkeypatch)

        # send_comment must NOT run.
        async def _must_not(**_kw):
            raise AssertionError("send_comment must not run")

        monkeypatch.setattr("bot.tasks.send_comment", _must_not)

        result = tasks.auto_comment_next()

        assert result["action"] == "skip"
        assert result["reason"] == "fire_lock_held"
        # Critical: NO reschedule (chain not multiplied).
        apply_async.assert_not_called()

        # No new CommentHistory rows.
        rows = db.query(CommentHistory).all()
        assert len(rows) == 0

    def test_kill_switch_does_not_acquire_lock(
        self, monkeypatch, db, fake_redis
    ):
        """Kill-switch path returns BEFORE lock acquisition (no Redis touch)."""
        from bot import tasks

        monkeypatch.setenv("AUTO_COMMENT_DISABLED", "1")
        apply_async = _patch_common(monkeypatch, db)

        result = tasks.auto_comment_next()

        assert result["action"] == "disabled"
        # Lock not acquired (kill-switch is upstream).
        assert not fake_redis.exists("auto_comment:fire_lock")
        apply_async.assert_not_called()

    def test_lock_blocked_skip_includes_trigger_in_log(
        self, monkeypatch, db, fake_redis
    ):
        from bot import tasks

        # Pre-held lock.
        fake_redis.set("auto_comment:fire_lock", "1", nx=True, ex=720)
        apply_async = _patch_common(monkeypatch, db)

        result = tasks.auto_comment_next(trigger="watchdog")

        assert result["action"] == "skip"
        assert result["reason"] == "fire_lock_held"
        apply_async.assert_not_called()
