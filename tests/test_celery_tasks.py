"""Tests for Celery tasks."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from bot.modules.collector import CollectorResult


class TestCollectAllTargets:
    @patch("bot.tasks._build_components")
    def test_no_runnable_targets(self, mock_build):
        from bot.tasks import collect_all_targets

        mock_scheduler = MagicMock()
        mock_scheduler.get_runnable_targets.return_value = []
        mock_build.return_value = (mock_scheduler, MagicMock(), MagicMock(), MagicMock())

        result = collect_all_targets()
        assert result["status"] == "no_targets"
        assert result["collected"] == 0

    @patch("bot.tasks._build_components")
    @patch("bot.tasks.asyncio.run")
    def test_successful_collection(self, mock_asyncio_run, mock_build):
        from bot.tasks import collect_all_targets

        targets = [{"id": "t1", "priority": 10, "enabled": True}]
        mock_scheduler = MagicMock()
        mock_scheduler.get_runnable_targets.return_value = targets

        mock_pipeline = MagicMock()
        mock_pipeline.process_batch.return_value = {
            "results": [
                {"fb_post_id": "p1", "status": "QUEUED", "score": 0.8},
                {"fb_post_id": "p2", "status": "FILTERED_OUT", "score": 0.1},
            ],
            "total": 2,
            "queued": 1,
            "filtered": 1,
        }

        mock_orchestrator = MagicMock()
        mock_orchestrator.process.return_value = {"draft_status": "PENDING_REVIEW"}

        mock_collector = MagicMock()
        mock_build.return_value = (mock_scheduler, mock_collector, mock_pipeline, mock_orchestrator)

        # Simulate async collection returning results
        collected_posts = [
            {"fb_post_id": "p1", "text_snippet": "Post 1"},
            {"fb_post_id": "p2", "text_snippet": "Post 2"},
        ]
        mock_asyncio_run.return_value = [
            CollectorResult("t1", collected_posts, success=True)
        ]

        result = collect_all_targets()
        assert result["status"] == "completed"
        assert result["collected"] == 2
        assert result["queued"] == 1
        assert result["drafted"] == 1
        mock_scheduler.mark_run.assert_called_once_with("t1")

    @patch("bot.tasks._build_components")
    @patch("bot.tasks.asyncio.run")
    def test_failed_collection_skipped(self, mock_asyncio_run, mock_build):
        from bot.tasks import collect_all_targets

        targets = [{"id": "t1", "priority": 10, "enabled": True}]
        mock_scheduler = MagicMock()
        mock_scheduler.get_runnable_targets.return_value = targets

        mock_build.return_value = (mock_scheduler, MagicMock(), MagicMock(), MagicMock())

        # Collection failed
        mock_asyncio_run.return_value = [
            CollectorResult("t1", [], success=False, error="timeout")
        ]

        result = collect_all_targets()
        assert result["status"] == "completed"
        assert result["collected"] == 0
        assert result["queued"] == 0

    @patch("bot.tasks._build_components")
    @patch("bot.tasks.asyncio.run")
    def test_multiple_targets(self, mock_asyncio_run, mock_build):
        from bot.tasks import collect_all_targets

        targets = [
            {"id": "t1", "priority": 10, "enabled": True},
            {"id": "t2", "priority": 5, "enabled": True},
        ]
        mock_scheduler = MagicMock()
        mock_scheduler.get_runnable_targets.return_value = targets

        mock_pipeline = MagicMock()
        mock_pipeline.process_batch.return_value = {
            "results": [{"fb_post_id": "p1", "status": "QUEUED", "score": 0.9}],
            "total": 1,
            "queued": 1,
            "filtered": 0,
        }

        mock_orchestrator = MagicMock()
        mock_orchestrator.process.return_value = {"draft_status": "PENDING_REVIEW"}

        mock_build.return_value = (mock_scheduler, MagicMock(), mock_pipeline, mock_orchestrator)

        mock_asyncio_run.return_value = [
            CollectorResult("t1", [{"fb_post_id": "p1"}], success=True),
            CollectorResult("t2", [], success=True),  # No posts found
        ]

        result = collect_all_targets()
        assert result["targets_attempted"] == 2
        assert result["targets_succeeded"] == 2
        assert result["collected"] == 1


class TestCollectSingleTarget:
    @patch("bot.tasks._build_components")
    def test_target_not_found(self, mock_build):
        from bot.tasks import collect_single_target

        mock_scheduler = MagicMock()
        mock_scheduler.get_target_by_id.return_value = None
        mock_build.return_value = (mock_scheduler, MagicMock(), MagicMock(), MagicMock())

        result = collect_single_target("nonexistent")
        assert result["status"] == "error"
        assert "not found" in result["error"]

    @patch("bot.tasks._build_components")
    @patch("bot.tasks.asyncio.run")
    def test_successful_single_collection(self, mock_asyncio_run, mock_build):
        from bot.tasks import collect_single_target

        target = {"id": "t1", "name": "Test", "mode": "scrape_public"}
        mock_scheduler = MagicMock()
        mock_scheduler.get_target_by_id.return_value = target

        mock_pipeline = MagicMock()
        mock_pipeline.process_batch.return_value = {
            "results": [{"fb_post_id": "p1", "status": "QUEUED"}],
            "total": 1,
            "queued": 1,
            "filtered": 0,
        }

        mock_orchestrator = MagicMock()
        mock_orchestrator.process.return_value = {"draft_status": "PENDING_REVIEW"}

        mock_collector = MagicMock()
        mock_build.return_value = (mock_scheduler, mock_collector, mock_pipeline, mock_orchestrator)

        mock_asyncio_run.return_value = CollectorResult(
            "t1", [{"fb_post_id": "p1"}], success=True
        )

        result = collect_single_target("t1")
        assert result["status"] == "completed"
        assert result["target_id"] == "t1"
        assert result["collected"] == 1
        assert result["queued"] == 1
        assert result["drafted"] == 1

    @patch("bot.tasks._build_components")
    @patch("bot.tasks.asyncio.run")
    def test_blocked_collection(self, mock_asyncio_run, mock_build):
        from bot.tasks import collect_single_target

        target = {"id": "t1", "name": "Test", "mode": "scrape_public"}
        mock_scheduler = MagicMock()
        mock_scheduler.get_target_by_id.return_value = target

        mock_build.return_value = (mock_scheduler, MagicMock(), MagicMock(), MagicMock())

        mock_asyncio_run.return_value = CollectorResult(
            "t1", [], success=False, error="captcha", blocked=True
        )

        result = collect_single_target("t1")
        assert result["status"] == "failed"
        assert result["blocked"] is True
