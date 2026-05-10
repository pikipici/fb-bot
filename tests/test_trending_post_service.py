"""Tests for TrendingPostService — upsert posts, preserve user status."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.models import Base, Source, TrendingPost
from server.services.trending_post_service import (
    TrendingPostService,
    UpsertResult,
)


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def source(db):
    src = Source(type="home_feed", label="Beranda")
    db.add(src)
    db.commit()
    db.refresh(src)
    return src


@pytest.fixture
def svc(db):
    return TrendingPostService(db)


def _now():
    return datetime.now(timezone.utc)


def _raw_post(
    fb_post_id: str = "abc123",
    reactions_total: int = 150,
    likes: int = 100,
    comments: int = 30,
    shares: int = 20,
    age_hours: float = 1.0,
) -> dict:
    return {
        "fb_post_id": fb_post_id,
        "author_name": "Tester",
        "author_fb_id": "999",
        "text": "Promo seru banget",
        "post_url": f"https://fb.com/{fb_post_id}",
        "thumbnail_url": None,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "reactions_total": reactions_total,
        "post_timestamp": _now() - timedelta(hours=age_hours),
    }


class TestUpsertInsert:
    def test_inserts_new_trending_post(self, svc, source, db):
        post = _raw_post()
        result = svc.upsert(source.id, post)
        assert isinstance(result, UpsertResult)
        assert result.inserted == 1
        assert result.updated == 0
        assert result.skipped == 0

        rows = db.query(TrendingPost).all()
        assert len(rows) == 1
        saved = rows[0]
        assert saved.fb_post_id == "abc123"
        assert saved.source_id == source.id
        assert saved.status == "NEW"
        assert saved.reactions_total == 150
        assert saved.score > 0
        assert saved.velocity > 0

    def test_filters_non_trending_with_skip(self, svc, source, db):
        """Posts below threshold are skipped — never enter DB."""
        weak = _raw_post(
            fb_post_id="weak1",
            reactions_total=5,
            likes=5,
            comments=0,
            shares=0,
        )
        result = svc.upsert(source.id, weak)
        assert result.inserted == 0
        assert result.skipped == 1
        assert db.query(TrendingPost).count() == 0

    def test_upsert_batch_mixed(self, svc, source, db):
        posts = [
            _raw_post(fb_post_id="hot1", reactions_total=200),
            _raw_post(fb_post_id="hot2", reactions_total=150),
            _raw_post(
                fb_post_id="cold1",
                reactions_total=5,
                likes=5,
                comments=0,
                shares=0,
            ),
        ]
        result = svc.upsert_batch(source.id, posts)
        assert result.inserted == 2
        assert result.skipped == 1
        assert db.query(TrendingPost).count() == 2


class TestUpsertUpdate:
    def test_updates_metrics_on_re_scan(self, svc, source, db):
        initial = _raw_post(reactions_total=150)
        svc.upsert(source.id, initial)

        # Second scan: higher engagement
        updated = _raw_post(reactions_total=300, likes=200)
        result = svc.upsert(source.id, updated)

        assert result.inserted == 0
        assert result.updated == 1

        row = db.query(TrendingPost).filter_by(fb_post_id="abc123").one()
        assert row.reactions_total == 300
        assert row.likes == 200

    def test_preserves_user_status_on_update(self, svc, source, db):
        """Once user marks DRAFTED/COMMENTED/SKIPPED, re-scan must NOT
        revert status to NEW.
        """
        initial = _raw_post()
        svc.upsert(source.id, initial)

        row = db.query(TrendingPost).filter_by(fb_post_id="abc123").one()
        row.status = "DRAFTED"
        db.commit()

        # Re-scan with different metrics
        updated = _raw_post(reactions_total=500)
        svc.upsert(source.id, updated)

        row = db.query(TrendingPost).filter_by(fb_post_id="abc123").one()
        assert row.status == "DRAFTED"
        assert row.reactions_total == 500  # metrics still refreshed

    def test_update_recomputes_score_and_velocity(self, svc, source, db):
        initial = _raw_post(reactions_total=100, age_hours=2.0)
        svc.upsert(source.id, initial)
        initial_row = (
            db.query(TrendingPost).filter_by(fb_post_id="abc123").one()
        )
        initial_score = initial_row.score
        initial_velocity = initial_row.velocity

        updated = _raw_post(reactions_total=500, age_hours=2.0)
        svc.upsert(source.id, updated)

        row = db.query(TrendingPost).filter_by(fb_post_id="abc123").one()
        assert row.score > initial_score
        assert row.velocity > initial_velocity


class TestKeywordFilter:
    def test_skips_post_not_matching_source_include(self, svc, source, db):
        """Keyword filter is applied at upsert time based on source rules."""
        source.keywords_include = '["laptop"]'
        db.commit()

        post = _raw_post()
        post["text"] = "jual mobil bekas"
        result = svc.upsert(source.id, post)
        assert result.skipped == 1
        assert db.query(TrendingPost).count() == 0

    def test_matches_include_is_upserted(self, svc, source, db):
        source.keywords_include = '["laptop"]'
        db.commit()

        post = _raw_post()
        post["text"] = "Jual laptop gaming murah"
        result = svc.upsert(source.id, post)
        assert result.inserted == 1

    def test_excluded_keyword_skips_even_if_trending(self, svc, source, db):
        source.keywords_exclude = '["rusak"]'
        db.commit()

        post = _raw_post()
        post["text"] = "laptop rusak harga nego"
        result = svc.upsert(source.id, post)
        assert result.skipped == 1


class TestUnsupportedUrlFilter:
    """Scanner must drop Stories/Reels/Watch before inserting.

    These FB URL shapes have no comment composer in the DOM, so letting
    them into ``trending_posts`` just creates dead cards that can only be
    Skipped. Filter applies equally to inserts and re-scans of existing
    rows (idempotent upsert should not silently resurrect them).
    """

    @pytest.mark.parametrize(
        "bad_url",
        [
            "https://www.facebook.com/stories/122112357512213503/abc",
            "https://m.facebook.com/stories/1/foo",
            "https://www.facebook.com/reel/1234567890",
            "https://www.facebook.com/reels/1234567890/",
            "https://www.facebook.com/watch/?v=1234567890",
            "https://www.facebook.com/share/r/abcDEF/",
            "https://www.facebook.com/share/v/abcDEF/",
        ],
    )
    def test_insert_unsupported_url_is_skipped(
        self, svc, source, db, bad_url
    ):
        post = _raw_post(fb_post_id="bad_shape")
        post["post_url"] = bad_url
        result = svc.upsert(source.id, post)
        assert result.skipped == 1
        assert db.query(TrendingPost).count() == 0

    def test_supported_url_still_inserted(self, svc, source, db):
        """Regression guard — normal permalinks must NOT be filtered."""
        post = _raw_post(fb_post_id="ok_perma")
        post["post_url"] = (
            "https://www.facebook.com/permalink.php?story_fbid=1&id=2"
        )
        result = svc.upsert(source.id, post)
        assert result.inserted == 1

    def test_missing_post_url_is_not_blocked_by_filter(
        self, svc, source, db
    ):
        """No ``post_url`` means we can't classify — don't filter on that."""
        post = _raw_post(fb_post_id="no_url")
        post["post_url"] = None
        result = svc.upsert(source.id, post)
        # Null URL can still happen (e.g. private sources). Upstream
        # logic handles the missing URL — don't short-circuit here.
        assert result.inserted == 1


class TestListTrending:
    def test_list_orders_by_score_desc(self, svc, source, db):
        posts = [
            _raw_post(fb_post_id="p1", reactions_total=100),
            _raw_post(fb_post_id="p2", reactions_total=500),
            _raw_post(fb_post_id="p3", reactions_total=250),
        ]
        svc.upsert_batch(source.id, posts)

        rows = svc.list_trending()
        ids = [r.fb_post_id for r in rows]
        assert ids[0] == "p2"  # highest score first

    def test_list_filters_by_status(self, svc, source, db):
        svc.upsert(source.id, _raw_post(fb_post_id="p1"))
        svc.upsert(source.id, _raw_post(fb_post_id="p2", reactions_total=300))

        db.query(TrendingPost).filter_by(fb_post_id="p1").update(
            {"status": "SKIPPED"}
        )
        db.commit()

        new_posts = svc.list_trending(status="NEW")
        assert len(new_posts) == 1
        assert new_posts[0].fb_post_id == "p2"
