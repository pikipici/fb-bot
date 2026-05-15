"""Phase K-1 — AutoCommentService.pick_next_eligible_post tests.

Eligibility rules:
- TrendingPost.status == 'NEW'
- No CommentHistory row exists for the post (any status: SENT/FAILED/PENDING)
- Order by collected_at ASC (oldest first FIFO)
- Returns None if no candidates
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.database import Base
from server.models import CommentHistory, Source, TrendingPost


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path}/test_autocomment.db",
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
def seeded_source(db_session):
    source = Source(
        type="home_feed",
        label="beranda",
        url="https://www.facebook.com/home.php",
        enabled=True,
    )
    db_session.add(source)
    db_session.commit()
    return source


def _make_post(
    db_session,
    source,
    *,
    fb_post_id: str,
    status: str = "NEW",
    collected_at: datetime | None = None,
) -> TrendingPost:
    post = TrendingPost(
        fb_post_id=fb_post_id,
        source_id=source.id,
        author_name="Test Author",
        text_snippet="dummy post",
        post_url=f"https://www.facebook.com/test/{fb_post_id}",
        status=status,
        collected_at=collected_at or datetime.now(timezone.utc),
    )
    db_session.add(post)
    db_session.commit()
    return post


def _make_history(db_session, post, *, status: str = "SENT") -> CommentHistory:
    row = CommentHistory(
        trending_post_id=post.id,
        comment_text="dummy",
        status=status,
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def service(db_session):
    from server.services.auto_comment_service import AutoCommentService

    return AutoCommentService(db_session)


class TestPickNextEligiblePost:
    def test_empty_table_returns_none(self, service):
        assert service.pick_next_eligible_post() is None

    def test_only_commented_status_returns_none(
        self, db_session, seeded_source, service
    ):
        _make_post(db_session, seeded_source, fb_post_id="p1", status="COMMENTED")
        _make_post(db_session, seeded_source, fb_post_id="p2", status="SKIPPED")
        _make_post(db_session, seeded_source, fb_post_id="p3", status="DRAFTED")
        assert service.pick_next_eligible_post() is None

    def test_single_new_with_no_history_returns_post(
        self, db_session, seeded_source, service
    ):
        post = _make_post(
            db_session, seeded_source, fb_post_id="p1", status="NEW"
        )
        result = service.pick_next_eligible_post()
        assert result is not None
        assert result.id == post.id

    def test_new_with_sent_history_skipped(
        self, db_session, seeded_source, service
    ):
        post = _make_post(
            db_session, seeded_source, fb_post_id="p1", status="NEW"
        )
        _make_history(db_session, post, status="SENT")
        assert service.pick_next_eligible_post() is None

    def test_new_with_failed_history_skipped(
        self, db_session, seeded_source, service
    ):
        post = _make_post(
            db_session, seeded_source, fb_post_id="p1", status="NEW"
        )
        _make_history(db_session, post, status="FAILED")
        assert service.pick_next_eligible_post() is None

    def test_new_with_pending_history_skipped(
        self, db_session, seeded_source, service
    ):
        post = _make_post(
            db_session, seeded_source, fb_post_id="p1", status="NEW"
        )
        _make_history(db_session, post, status="PENDING")
        assert service.pick_next_eligible_post() is None

    def test_picks_oldest_collected_at_first(
        self, db_session, seeded_source, service
    ):
        now = datetime.now(timezone.utc)
        newest = _make_post(
            db_session,
            seeded_source,
            fb_post_id="p_newest",
            status="NEW",
            collected_at=now,
        )
        oldest = _make_post(
            db_session,
            seeded_source,
            fb_post_id="p_oldest",
            status="NEW",
            collected_at=now - timedelta(hours=2),
        )
        middle = _make_post(
            db_session,
            seeded_source,
            fb_post_id="p_middle",
            status="NEW",
            collected_at=now - timedelta(hours=1),
        )

        result = service.pick_next_eligible_post()
        assert result is not None
        assert result.id == oldest.id

    def test_mixed_eligible_skips_history_picks_oldest_clean(
        self, db_session, seeded_source, service
    ):
        now = datetime.now(timezone.utc)
        post_old_with_history = _make_post(
            db_session,
            seeded_source,
            fb_post_id="p_old_dirty",
            status="NEW",
            collected_at=now - timedelta(hours=3),
        )
        _make_history(db_session, post_old_with_history, status="FAILED")

        post_clean = _make_post(
            db_session,
            seeded_source,
            fb_post_id="p_clean",
            status="NEW",
            collected_at=now - timedelta(hours=1),
        )
        _make_post(
            db_session,
            seeded_source,
            fb_post_id="p_newest",
            status="NEW",
            collected_at=now,
        )

        result = service.pick_next_eligible_post()
        assert result is not None
        assert result.id == post_clean.id
