"""Integration tests for Celery tasks.

These tests exercise the real ``Orchestrator`` path (pipeline → save → draft)
against an in-memory SQLite database. Only the outermost I/O surfaces — the
scheduler's target loader and the collector's network fetch — are mocked.

This is deliberate: the earlier pure-MagicMock suite let the runtime crash
of ``Orchestrator()`` (missing ``db`` argument) pass silently. We now wire
up the full DB + orchestrator stack so contract changes surface as failures.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from bot.modules.collector import CollectorResult
from server import database as database_module
from server.database import Base


@pytest.fixture
def in_memory_db():
    """Bind ``server.database.SessionLocal`` to a fresh in-memory SQLite DB.

    Each test gets its own isolated schema so state never leaks between
    tasks. We monkey-patch the module-level ``SessionLocal`` because
    ``bot.tasks`` imports it by name at task invocation.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=engine
    )

    original = database_module.SessionLocal
    database_module.SessionLocal = TestingSessionLocal
    try:
        # Also patch the reference that ``bot.tasks`` grabbed at import time.
        import bot.tasks as tasks_module
        original_task_sl = tasks_module.SessionLocal
        tasks_module.SessionLocal = TestingSessionLocal
        try:
            yield TestingSessionLocal
        finally:
            tasks_module.SessionLocal = original_task_sl
    finally:
        database_module.SessionLocal = original
        engine.dispose()


def _make_raw_post(
    fb_post_id: str,
    target_id: str = "t1",
    text: str = "Saya cari jasa desain logo untuk project",
    likes: int = 20,
    comments: int = 10,
    shares: int = 5,
) -> dict[str, Any]:
    """Build a post dict shaped like Parser output."""
    from datetime import datetime, timezone, timedelta

    return {
        "fb_post_id": fb_post_id,
        "target_id": target_id,
        "url": f"https://facebook.com/{fb_post_id}",
        "author_id": "author_1",
        "author_name": "Test Author",
        "text_snippet": text,
        "timestamp": (
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).isoformat(),
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "language": "id",
        "source_mode": "scrape",
    }


class TestCollectAllTargetsIntegration:
    def test_no_runnable_targets(self, in_memory_db):
        from bot.tasks import collect_all_targets

        with patch("bot.tasks._build_scheduling_components") as mock_build:
            scheduler = MagicMock()
            scheduler.get_runnable_targets.return_value = []
            mock_build.return_value = (scheduler, MagicMock())

            result = collect_all_targets()

        assert result["status"] == "no_targets"
        assert result["collected"] == 0
        assert result["queued"] == 0
        assert result["drafted"] == 0

    def test_successful_collection_persists_posts_and_drafts(self, in_memory_db):
        """End-to-end: parser output → orchestrator → DB rows for posts + drafts."""
        from bot.tasks import collect_all_targets
        from server.models import Post, Draft

        targets = [{"id": "t1", "priority": 10, "enabled": True}]
        raw_posts = [
            _make_raw_post("p_queued_1"),
            _make_raw_post(
                "p_filtered",
                text="Cuaca hari ini cerah",
                likes=0, comments=0, shares=0,
            ),
        ]

        with patch("bot.tasks._build_scheduling_components") as mock_build, \
                patch("bot.tasks.asyncio.run") as mock_run:
            scheduler = MagicMock()
            scheduler.get_runnable_targets.return_value = targets
            mock_build.return_value = (scheduler, MagicMock())
            mock_run.return_value = [
                CollectorResult("t1", raw_posts, success=True),
            ]

            result = collect_all_targets()

        assert result["status"] == "completed"
        assert result["collected"] == 2
        assert result["queued"] >= 1
        assert result["drafted"] >= 1
        scheduler.mark_run.assert_called_once_with("t1")

        # Verify DB side-effects against the same in-memory engine.
        session = in_memory_db()
        try:
            posts = session.query(Post).all()
            drafts = session.query(Draft).all()
            assert len(posts) >= 1
            assert any(p.fb_post_id == "p_queued_1" for p in posts)
            # text_snippet must NOT be empty (guards the prior save_post bug).
            queued_post = next(p for p in posts if p.fb_post_id == "p_queued_1")
            assert queued_post.text_snippet
            assert "jasa desain" in queued_post.text_snippet.lower()
            assert len(drafts) >= 1
            assert drafts[0].post_id == queued_post.id
        finally:
            session.close()

    def test_failed_collection_is_skipped(self, in_memory_db):
        from bot.tasks import collect_all_targets

        targets = [{"id": "t1", "priority": 10, "enabled": True}]
        with patch("bot.tasks._build_scheduling_components") as mock_build, \
                patch("bot.tasks.asyncio.run") as mock_run:
            scheduler = MagicMock()
            scheduler.get_runnable_targets.return_value = targets
            mock_build.return_value = (scheduler, MagicMock())
            mock_run.return_value = [
                CollectorResult("t1", [], success=False, error="timeout"),
            ]

            result = collect_all_targets()

        assert result["status"] == "completed"
        assert result["collected"] == 0
        assert result["queued"] == 0
        assert result["drafted"] == 0
        # Failed targets must NOT be marked as run.
        scheduler.mark_run.assert_not_called()

    def test_multiple_targets_summary(self, in_memory_db):
        from bot.tasks import collect_all_targets

        targets = [
            {"id": "t1", "priority": 10, "enabled": True},
            {"id": "t2", "priority": 5, "enabled": True},
        ]
        with patch("bot.tasks._build_scheduling_components") as mock_build, \
                patch("bot.tasks.asyncio.run") as mock_run:
            scheduler = MagicMock()
            scheduler.get_runnable_targets.return_value = targets
            mock_build.return_value = (scheduler, MagicMock())
            mock_run.return_value = [
                CollectorResult("t1", [_make_raw_post("m1", "t1")], success=True),
                CollectorResult("t2", [], success=True),  # No posts found
            ]

            result = collect_all_targets()

        assert result["targets_attempted"] == 2
        assert result["targets_succeeded"] == 2
        assert result["collected"] == 1


class TestCollectSingleTargetIntegration:
    def test_target_not_found(self, in_memory_db):
        from bot.tasks import collect_single_target

        with patch("bot.tasks._build_scheduling_components") as mock_build:
            scheduler = MagicMock()
            scheduler.get_target_by_id.return_value = None
            mock_build.return_value = (scheduler, MagicMock())

            result = collect_single_target("missing")

        assert result["status"] == "error"
        assert "not found" in result["error"]

    def test_successful_single_collection(self, in_memory_db):
        from bot.tasks import collect_single_target
        from server.models import Post

        target = {"id": "t1", "name": "Test", "mode": "scrape_public"}
        with patch("bot.tasks._build_scheduling_components") as mock_build, \
                patch("bot.tasks.asyncio.run") as mock_run:
            scheduler = MagicMock()
            scheduler.get_target_by_id.return_value = target
            mock_build.return_value = (scheduler, MagicMock())
            mock_run.return_value = CollectorResult(
                "t1", [_make_raw_post("s1")], success=True,
            )

            result = collect_single_target("t1")

        assert result["status"] == "completed"
        assert result["target_id"] == "t1"
        assert result["collected"] == 1
        assert result["queued"] >= 1
        assert result["drafted"] >= 1

        session = in_memory_db()
        try:
            saved = session.query(Post).filter(Post.fb_post_id == "s1").first()
            assert saved is not None
            assert saved.text_snippet  # regression: must NOT be empty
        finally:
            session.close()

    def test_blocked_collection_returns_failed_status(self, in_memory_db):
        from bot.tasks import collect_single_target

        target = {"id": "t1", "name": "Test", "mode": "scrape_public"}
        with patch("bot.tasks._build_scheduling_components") as mock_build, \
                patch("bot.tasks.asyncio.run") as mock_run:
            scheduler = MagicMock()
            scheduler.get_target_by_id.return_value = target
            mock_build.return_value = (scheduler, MagicMock())
            mock_run.return_value = CollectorResult(
                "t1", [], success=False, error="captcha", blocked=True,
            )

            result = collect_single_target("t1")

        assert result["status"] == "failed"
        assert result["blocked"] is True
