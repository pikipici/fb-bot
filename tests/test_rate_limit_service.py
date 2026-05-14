"""Tests for RateLimitService — 5 comments / 6 hour rolling window."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.database import Base
from server.models import (
    CommentHistory,
    Source,
    TrendingPost,
    User,
)


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path}/test_ratelimit.db",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    try:
        db = SessionLocal()
        yield db
        db.close()
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture
def seeded_post(db_session):
    source = Source(
        type="home_feed",
        label="beranda",
        url="https://www.facebook.com/home.php",
        enabled=True,
    )
    db_session.add(source)
    db_session.commit()

    post = TrendingPost(
        fb_post_id="pfbid_test_1",
        source_id=source.id,
        author_name="Test Author",
        text_snippet="dummy post",
        post_url="https://www.facebook.com/test/post/1",
        status="DRAFTED",
    )
    db_session.add(post)
    db_session.commit()
    return post


@pytest.fixture
def seeded_user(db_session):
    user = User(
        username="admin",
        password_hash="$2b$12$dummy",
        role="admin",
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def service(db_session):
    from server.services.rate_limit_service import RateLimitService

    return RateLimitService(db_session)


def _seed_send(db_session, post, *, minutes_ago: int, status: str = "SENT"):
    sent_at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    row = CommentHistory(
        trending_post_id=post.id,
        comment_text="x",
        status=status,
        sent_at=sent_at,
    )
    db_session.add(row)
    db_session.commit()
    return row


class TestConstants:
    def test_max_and_window_hours_match_mvp_spec(self):
        from server.services.rate_limit_service import (
            MAX_COMMENTS_PER_WINDOW,
            WINDOW_HOURS,
        )

        assert MAX_COMMENTS_PER_WINDOW == 5
        assert WINDOW_HOURS == 6


class TestCheckAllowed:
    def test_allows_when_empty(self, service):
        status = service.check_allowed()
        assert status.allowed is True
        assert status.used == 0
        assert status.remaining == 5

    def test_allows_after_4_sent(self, service, db_session, seeded_post):
        for i in range(4):
            _seed_send(db_session, seeded_post, minutes_ago=30 + i)
        status = service.check_allowed()
        assert status.allowed is True
        assert status.used == 4
        assert status.remaining == 1

    def test_blocks_when_5_sent_in_window(
        self, service, db_session, seeded_post
    ):
        for i in range(5):
            _seed_send(db_session, seeded_post, minutes_ago=10 + i)
        status = service.check_allowed()
        assert status.allowed is False
        assert status.used == 5
        assert status.remaining == 0
        assert status.resets_at is not None

    def test_ignores_sends_outside_window(
        self, service, db_session, seeded_post
    ):
        # 5 sends but all older than 6h — should be ignored
        for i in range(5):
            _seed_send(db_session, seeded_post, minutes_ago=60 * 7 + i)
        status = service.check_allowed()
        assert status.allowed is True
        assert status.used == 0

    def test_ignores_failed_status(self, service, db_session, seeded_post):
        # FAILED sends shouldn't count against quota
        for i in range(5):
            _seed_send(
                db_session, seeded_post, minutes_ago=10 + i, status="FAILED"
            )
        status = service.check_allowed()
        assert status.allowed is True
        assert status.used == 0


class TestWindowStats:
    def test_shape(self, service):
        stats = service.window_stats()
        assert stats["limit"] == 5
        assert stats["window_hours"] == 6
        assert stats["used"] == 0
        assert stats["remaining"] == 5
        assert stats["allowed"] is True
        assert "resets_at" in stats

    def test_resets_at_is_oldest_send_plus_window(
        self, service, db_session, seeded_post
    ):
        # Oldest send was 100 minutes ago → resets 6h - 100m = 260m from now
        oldest = _seed_send(db_session, seeded_post, minutes_ago=100)
        _seed_send(db_session, seeded_post, minutes_ago=30)

        stats = service.window_stats()
        assert stats["resets_at"] is not None
        oldest_sent = oldest.sent_at
        if oldest_sent.tzinfo is None:
            oldest_sent = oldest_sent.replace(tzinfo=timezone.utc)
        expected = oldest_sent + timedelta(hours=6)
        resets = stats["resets_at"]
        if resets.tzinfo is None:
            resets = resets.replace(tzinfo=timezone.utc)
        # tolerate 5s drift
        delta = abs((resets - expected).total_seconds())
        assert delta < 5

    def test_resets_at_none_when_empty(self, service):
        stats = service.window_stats()
        assert stats["resets_at"] is None


class TestRecordSend:
    def test_inserts_sent_row(
        self, service, db_session, seeded_post, seeded_user
    ):
        row = service.record_send(
            trending_post_id=seeded_post.id,
            comment_text="halo bro",
            user_id=seeded_user.id,
            fb_comment_id="cmt_123",
        )
        assert row.id is not None
        assert row.trending_post_id == seeded_post.id
        assert row.comment_text == "halo bro"
        assert row.status == "SENT"
        assert row.fb_comment_id == "cmt_123"
        assert row.user_id == seeded_user.id
        assert row.sent_at is not None

    def test_defaults_status_sent(self, service, seeded_post):
        row = service.record_send(
            trending_post_id=seeded_post.id,
            comment_text="hi",
        )
        assert row.status == "SENT"

    def test_explicit_failed_status(self, service, seeded_post):
        row = service.record_send(
            trending_post_id=seeded_post.id,
            comment_text="hi",
            status="FAILED",
            error_message="checkpoint required",
        )
        assert row.status == "FAILED"
        assert row.error_message == "checkpoint required"

    def test_flips_trending_post_status_to_commented(
        self, service, db_session, seeded_post
    ):
        service.record_send(
            trending_post_id=seeded_post.id,
            comment_text="hi",
        )
        db_session.refresh(seeded_post)
        assert seeded_post.status == "COMMENTED"

    def test_failed_does_not_flip_post_status(
        self, service, db_session, seeded_post
    ):
        original = seeded_post.status
        service.record_send(
            trending_post_id=seeded_post.id,
            comment_text="hi",
            status="FAILED",
        )
        db_session.refresh(seeded_post)
        assert seeded_post.status == original


class TestRateLimitExceeded:
    def test_record_send_raises_when_window_full(
        self, service, db_session, seeded_post
    ):
        from server.services.rate_limit_service import (
            RateLimitExceededError,
        )

        for i in range(5):
            _seed_send(db_session, seeded_post, minutes_ago=10 + i)

        with pytest.raises(RateLimitExceededError):
            service.record_send(
                trending_post_id=seeded_post.id,
                comment_text="boom",
            )

    def test_record_send_failed_allowed_when_window_full(
        self, service, db_session, seeded_post
    ):
        # FAILED rows shouldn't be blocked — we want to log the failure
        for i in range(5):
            _seed_send(db_session, seeded_post, minutes_ago=10 + i)

        row = service.record_send(
            trending_post_id=seeded_post.id,
            comment_text="boom",
            status="FAILED",
            error_message="rate limit preflight",
        )
        assert row.status == "FAILED"


class TestEnvOverride:
    """``MAX_COMMENTS_PER_WINDOW`` env var overrides the hardcoded default.

    Rationale: quota started as an anti-FB-block safety rail, but user wants
    an informational "komen hari ini" counter instead. Setting env to 9999
    effectively bypasses the preflight gate without ripping out the service
    (rollback-friendly: restore env to tighten limit again).
    """

    def test_env_override_respected_in_status(
        self, service, db_session, seeded_post, monkeypatch
    ):
        """Env var → QuotaStatus.limit reflects override, not constant 5."""
        monkeypatch.setenv("MAX_COMMENTS_PER_WINDOW", "9999")
        for i in range(5):
            _seed_send(db_session, seeded_post, minutes_ago=10 + i)

        status = service.check_allowed()

        assert status.allowed is True
        assert status.used == 5
        assert status.limit == 9999
        assert status.remaining == 9994

    def test_env_override_respected_in_record_send(
        self, service, db_session, seeded_post, monkeypatch
    ):
        """Env var → record_send doesn't raise when constant-default would."""
        monkeypatch.setenv("MAX_COMMENTS_PER_WINDOW", "9999")
        for i in range(5):
            _seed_send(db_session, seeded_post, minutes_ago=10 + i)

        # With default=5 this would raise RateLimitExceededError.
        row = service.record_send(
            trending_post_id=seeded_post.id,
            comment_text="overrun but allowed",
        )
        assert row.status == "SENT"

    def test_env_invalid_falls_back_to_default(
        self, service, db_session, seeded_post, monkeypatch
    ):
        """Garbage env value falls back to hardcoded default, not crash."""
        monkeypatch.setenv("MAX_COMMENTS_PER_WINDOW", "not-a-number")
        status = service.check_allowed()
        assert status.limit == 5

    def test_env_unset_uses_default(self, service, monkeypatch):
        """No env var → default 5 preserved."""
        monkeypatch.delenv("MAX_COMMENTS_PER_WINDOW", raising=False)
        status = service.check_allowed()
        assert status.limit == 5
        assert status.remaining == 5
