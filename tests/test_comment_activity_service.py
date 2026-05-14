"""Tests for CommentActivityService — calendar-day WIB counter.

Replaces rolling-window quota UX: user wants "Komen hari ini: X" info
widget instead of 5/6h preflight gate. Counts ``CommentHistory`` rows
with ``status='SENT'`` inside the current Asia/Jakarta calendar day.

WIB = UTC+7, no DST. Day boundary `00:00 WIB` = `17:00 UTC` of previous
calendar UTC day.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.database import Base
from server.models import CommentHistory, Source, TrendingPost


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path}/test_activity.db",
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
    src = Source(
        type="home_feed",
        label="beranda",
        url="https://www.facebook.com/home.php",
        enabled=True,
    )
    db_session.add(src)
    db_session.commit()
    post = TrendingPost(
        fb_post_id="pfbid_activity_1",
        source_id=src.id,
        author_name="Tester",
        text_snippet="x",
        post_url="https://facebook.com/x",
        status="DRAFTED",
    )
    db_session.add(post)
    db_session.commit()
    return post


def _seed_send(db_session, post, *, sent_at: datetime, status: str = "SENT"):
    row = CommentHistory(
        trending_post_id=post.id,
        comment_text="x",
        status=status,
        sent_at=sent_at,
    )
    db_session.add(row)
    db_session.commit()
    return row


class TestTodayCount:
    def test_empty_returns_zero(self, db_session):
        from server.services.comment_activity_service import (
            CommentActivityService,
        )

        svc = CommentActivityService(db_session)
        assert svc.today_count() == 0

    def test_counts_sent_within_today_wib(self, db_session, seeded_post):
        from server.services.comment_activity_service import (
            CommentActivityService,
        )

        wib = ZoneInfo("Asia/Jakarta")
        # Pick noon-WIB today, safely inside the WIB calendar day.
        now_wib = datetime.now(wib)
        midday_wib = now_wib.replace(hour=12, minute=0, second=0, microsecond=0)
        # Keep within today regardless of current local hour.
        if midday_wib > now_wib:
            midday_wib = now_wib - timedelta(minutes=5)

        for offset_min in (0, 10, 30):
            _seed_send(
                db_session,
                seeded_post,
                sent_at=(midday_wib - timedelta(minutes=offset_min)).astimezone(
                    timezone.utc
                ),
            )

        svc = CommentActivityService(db_session)
        assert svc.today_count() == 3

    def test_ignores_yesterday_wib(self, db_session, seeded_post):
        from server.services.comment_activity_service import (
            CommentActivityService,
        )

        wib = ZoneInfo("Asia/Jakarta")
        # 23:30 WIB yesterday = before today's 00:00 WIB boundary.
        yest_late_wib = (
            datetime.now(wib).replace(hour=23, minute=30, second=0, microsecond=0)
            - timedelta(days=1)
        )
        _seed_send(
            db_session,
            seeded_post,
            sent_at=yest_late_wib.astimezone(timezone.utc),
        )

        svc = CommentActivityService(db_session)
        assert svc.today_count() == 0

    def test_ignores_failed_status(self, db_session, seeded_post):
        from server.services.comment_activity_service import (
            CommentActivityService,
        )

        wib = ZoneInfo("Asia/Jakarta")
        noon = datetime.now(wib).replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        _seed_send(
            db_session,
            seeded_post,
            sent_at=noon.astimezone(timezone.utc),
            status="FAILED",
        )

        svc = CommentActivityService(db_session)
        assert svc.today_count() == 0

    def test_boundary_00_00_wib_is_17_00_utc(self, db_session, seeded_post):
        """00:00 WIB (UTC+7) = 17:00 UTC hari sebelumnya.

        Sends at 16:59 UTC = 23:59 WIB yesterday → excluded.
        Sends at 17:00 UTC = 00:00 WIB today → included.
        """
        from server.services.comment_activity_service import (
            CommentActivityService,
        )

        wib = ZoneInfo("Asia/Jakarta")
        today_wib_midnight = datetime.now(wib).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        today_wib_midnight_utc = today_wib_midnight.astimezone(timezone.utc)

        # 1 second before boundary → yesterday
        _seed_send(
            db_session,
            seeded_post,
            sent_at=today_wib_midnight_utc - timedelta(seconds=1),
        )
        # Exactly at boundary → today
        _seed_send(
            db_session,
            seeded_post,
            sent_at=today_wib_midnight_utc,
        )
        # 1 second after → today
        _seed_send(
            db_session,
            seeded_post,
            sent_at=today_wib_midnight_utc + timedelta(seconds=1),
        )

        svc = CommentActivityService(db_session)
        assert svc.today_count() == 2


class TestTodaySnapshot:
    """``today_snapshot()`` dict shape buat router response."""

    def test_shape_empty(self, db_session):
        from server.services.comment_activity_service import (
            CommentActivityService,
        )

        snap = CommentActivityService(db_session).today_snapshot()
        assert snap["count_today"] == 0
        assert snap["tz"] == "Asia/Jakarta"
        # date is today's WIB calendar date, ISO YYYY-MM-DD
        wib = ZoneInfo("Asia/Jakarta")
        assert snap["date"] == datetime.now(wib).date().isoformat()

    def test_shape_with_rows(self, db_session, seeded_post):
        from server.services.comment_activity_service import (
            CommentActivityService,
        )

        wib = ZoneInfo("Asia/Jakarta")
        noon = datetime.now(wib).replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        if noon > datetime.now(wib):
            noon = datetime.now(wib) - timedelta(minutes=5)
        for _ in range(4):
            _seed_send(db_session, seeded_post, sent_at=noon.astimezone(timezone.utc))

        snap = CommentActivityService(db_session).today_snapshot()
        assert snap["count_today"] == 4
