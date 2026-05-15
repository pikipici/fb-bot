"""Phase K-3 — auto_comment_next task + _enqueue_next_comment helper tests.

Mirror of Phase J self-rescheduling pattern but for the comment chain.

Pipeline per tick:
  1. Kill-switch (AUTO_COMMENT_DISABLED=1) → noop, no reschedule
  2. Pick eligible post → if none, skip + reschedule
  3. Pre-check rate limit → if blocked, skip + reschedule (no send)
  4. Pick ACTIVE FB account → if none, skip + reschedule
  5. Generate AI draft → on error: log FAILED, flip post=SKIPPED, reschedule
  6. Send comment via Playwright → on cookie expire: log FAILED, flip account
     EXPIRED, reschedule. On other error: log FAILED, flip post=SKIPPED.
  7. On success: record_send SENT (RateLimitService auto-flips post=COMMENTED)
  8. ALWAYS reschedule next tick in finally-block (even on raise).
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

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


def _seed_account(db, *, status: str = "ACTIVE") -> FBAccount:
    acc = FBAccount(
        label="khorur",
        status=status,
        cookies_encrypted="dummy_encrypted_blob",
        fb_user_id="61577777450562",
        fb_name="Digi Markt",
    )
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return acc


def _seed_post(
    db, *, fb_post_id: str = "p1", status: str = "NEW"
) -> TrendingPost:
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
        status=status,
        collected_at=datetime.now(timezone.utc),
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    return post


# ---------------------------------------------------------------------------
# _enqueue_next_comment helper
# ---------------------------------------------------------------------------


class TestEnqueueNextComment:
    def test_returns_countdown_in_config_range(self, monkeypatch):
        from bot import tasks

        monkeypatch.setenv("AUTO_COMMENT_MIN_INTERVAL_SECONDS", "100")
        monkeypatch.setenv("AUTO_COMMENT_MAX_INTERVAL_SECONDS", "200")
        monkeypatch.delenv("AUTO_COMMENT_DISABLED", raising=False)

        apply_async = MagicMock()
        monkeypatch.setattr(
            "bot.tasks.auto_comment_next.apply_async", apply_async
        )

        countdown = tasks._enqueue_next_comment()
        assert countdown is not None
        assert 100 <= countdown <= 200
        apply_async.assert_called_once()
        kwargs = apply_async.call_args.kwargs
        assert kwargs["kwargs"] == {"trigger": "selfsched"}
        assert kwargs["countdown"] == countdown

    def test_kill_switch_returns_none(self, monkeypatch):
        from bot import tasks

        monkeypatch.setenv("AUTO_COMMENT_DISABLED", "1")
        apply_async = MagicMock()
        monkeypatch.setattr(
            "bot.tasks.auto_comment_next.apply_async", apply_async
        )

        countdown = tasks._enqueue_next_comment()
        assert countdown is None
        apply_async.assert_not_called()

    def test_custom_source_in_kwargs(self, monkeypatch):
        from bot import tasks

        monkeypatch.delenv("AUTO_COMMENT_DISABLED", raising=False)
        apply_async = MagicMock()
        monkeypatch.setattr(
            "bot.tasks.auto_comment_next.apply_async", apply_async
        )

        tasks._enqueue_next_comment(source="watchdog")
        kwargs = apply_async.call_args.kwargs
        assert kwargs["kwargs"] == {"trigger": "watchdog"}


# ---------------------------------------------------------------------------
# auto_comment_next task body
# ---------------------------------------------------------------------------


class TestAutoCommentNext:
    """Each test verifies one branch + the universal "reschedule in finally"."""

    def _patch_common(self, monkeypatch, db):
        monkeypatch.setattr(
            "bot.tasks._db_session", lambda: _ctx_session(db)
        )
        # Capture the rescheduling call without actually firing.
        apply_async = MagicMock()
        monkeypatch.setattr(
            "bot.tasks.auto_comment_next.apply_async", apply_async
        )
        return apply_async

    def test_kill_switch_skips_pick_and_reschedule(self, monkeypatch, db):
        from bot import tasks

        monkeypatch.setenv("AUTO_COMMENT_DISABLED", "1")
        apply_async = self._patch_common(monkeypatch, db)

        result = tasks.auto_comment_next()

        assert result["action"] == "disabled"
        # No reschedule when paused (escape hatch).
        apply_async.assert_not_called()

    def test_no_eligible_post_skips_and_reschedules(self, monkeypatch, db):
        from bot import tasks

        apply_async = self._patch_common(monkeypatch, db)
        # No posts seeded.

        result = tasks.auto_comment_next()

        assert result["action"] == "skip"
        assert result["reason"] == "no_eligible"
        apply_async.assert_called_once()

    def test_rate_limit_exceeded_skips_send_and_reschedules(
        self, monkeypatch, db
    ):
        from bot import tasks

        _seed_account(db)
        _seed_post(db)
        apply_async = self._patch_common(monkeypatch, db)

        # Stub RateLimitService to report quota exceeded.
        class _StubRateSvc:
            def __init__(self, _db):
                pass

            def check_allowed(self):
                from server.services.rate_limit_service import QuotaStatus

                return QuotaStatus(
                    allowed=False,
                    used=5,
                    remaining=0,
                    limit=5,
                    window_hours=6,
                    resets_at=None,
                )

            def record_send(self, **_kwargs):  # should NOT be called
                raise AssertionError("record_send must not run when blocked")

        monkeypatch.setattr(
            "server.services.rate_limit_service.RateLimitService",
            _StubRateSvc,
        )

        result = tasks.auto_comment_next()

        assert result["action"] == "skip"
        assert result["reason"] == "rate_limited"
        apply_async.assert_called_once()

    def test_no_active_account_skips_and_reschedules(self, monkeypatch, db):
        from bot import tasks

        _seed_post(db)  # eligible post but no account
        apply_async = self._patch_common(monkeypatch, db)

        result = tasks.auto_comment_next()

        assert result["action"] == "skip"
        assert result["reason"] == "no_account"
        apply_async.assert_called_once()

    def test_ai_draft_error_logs_failed_and_skips_post(self, monkeypatch, db):
        from bot import tasks

        _seed_account(db)
        post = _seed_post(db)
        apply_async = self._patch_common(monkeypatch, db)

        # Stub AI draft to raise.
        from server.services.ai_draft_service import AIDraftUpstreamError

        class _StubAI:
            def __init__(self, _db):
                pass

            def generate(self, **_kwargs):
                raise AIDraftUpstreamError("LLM down")

        monkeypatch.setattr(
            "server.services.ai_draft_service.AIDraftService", _StubAI
        )
        monkeypatch.setattr(
            "bot.tasks.decrypt_cookies",
            lambda blob: {"c_user": "1", "xs": "x"},
        )

        result = tasks.auto_comment_next()

        # Reschedule still fires.
        apply_async.assert_called_once()

        # FAILED history row recorded.
        rows = db.query(CommentHistory).all()
        assert len(rows) == 1
        assert rows[0].status == "FAILED"
        assert "ai_draft" in (rows[0].error_message or "").lower()

        # Post flipped to SKIPPED so dedup query never picks it again.
        db.refresh(post)
        assert post.status == "SKIPPED"

        assert result["action"] == "failed"
        assert result["reason"] == "ai_draft_error"

    def test_cookie_expired_logs_failed_and_flips_account(
        self, monkeypatch, db
    ):
        from bot import tasks

        acc = _seed_account(db)
        post = _seed_post(db)
        apply_async = self._patch_common(monkeypatch, db)

        # Stub AI draft to return text.
        class _StubAI:
            def __init__(self, _db):
                pass

            def generate(self, **_kwargs):
                return "halo bro mantap"

        monkeypatch.setattr(
            "server.services.ai_draft_service.AIDraftService", _StubAI
        )
        monkeypatch.setattr(
            "bot.tasks.decrypt_cookies",
            lambda blob: {"c_user": "1", "xs": "x"},
        )

        # Stub send_comment to raise CookieExpiredError.
        from bot.modules.comment_sender import CookieExpiredError

        async def _raise(**_kwargs):
            raise CookieExpiredError("login wall")

        monkeypatch.setattr("bot.tasks.send_comment", _raise)

        result = tasks.auto_comment_next()

        # Reschedule still fires.
        apply_async.assert_called_once()

        # Account flipped to EXPIRED.
        db.refresh(acc)
        assert acc.status == "EXPIRED"

        # CommentHistory row FAILED + account_expired hint.
        rows = db.query(CommentHistory).all()
        assert len(rows) == 1
        assert rows[0].status == "FAILED"
        assert "cookie" in (rows[0].error_message or "").lower()

        # Post NOT flipped to SKIPPED (cookie problem isn't post's fault) —
        # next tick will short-circuit on no_account anyway.
        db.refresh(post)
        assert post.status == "NEW"

        assert result["action"] == "failed"
        assert result["reason"] == "cookie_expired"

    def test_successful_send_logs_sent_and_flips_post(self, monkeypatch, db):
        from bot import tasks

        _seed_account(db)
        post = _seed_post(db)
        apply_async = self._patch_common(monkeypatch, db)

        class _StubAI:
            def __init__(self, _db):
                pass

            def generate(self, **_kwargs):
                return "wah keren bro"

        monkeypatch.setattr(
            "server.services.ai_draft_service.AIDraftService", _StubAI
        )
        monkeypatch.setattr(
            "bot.tasks.decrypt_cookies",
            lambda blob: {"c_user": "1", "xs": "x"},
        )

        from bot.modules.comment_sender import SendResult

        async def _ok(**_kwargs):
            return SendResult(success=True, comment_id="fb_c_123", error=None)

        monkeypatch.setattr("bot.tasks.send_comment", _ok)

        result = tasks.auto_comment_next()

        apply_async.assert_called_once()

        # SENT row recorded.
        rows = db.query(CommentHistory).all()
        assert len(rows) == 1
        assert rows[0].status == "SENT"
        assert rows[0].comment_text == "wah keren bro"
        assert rows[0].fb_comment_id == "fb_c_123"

        # Post auto-flipped to COMMENTED via RateLimitService.record_send.
        db.refresh(post)
        assert post.status == "COMMENTED"

        assert result["action"] == "sent"
