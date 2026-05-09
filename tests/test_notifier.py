"""Tests for Notifier module."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.modules.notifier import Notifier


@pytest.fixture
def notifier():
    return Notifier(bot_token="test-token", chat_id="123456")


@pytest.fixture
def disabled_notifier():
    return Notifier(bot_token="", chat_id="")


class TestNotifierInit:
    def test_enabled_when_both_set(self, notifier):
        assert notifier.enabled is True

    def test_disabled_when_no_token(self, disabled_notifier):
        assert disabled_notifier.enabled is False

    def test_disabled_when_partial(self):
        n = Notifier(bot_token="token", chat_id="")
        assert n.enabled is False


class TestSendAlert:
    @pytest.mark.asyncio
    async def test_sends_with_correct_prefix(self, notifier):
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
            result = await notifier.send_alert("Test message", level="error")

        assert result is True
        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert "🚨" in payload["text"]
        assert "Test message" in payload["text"]

    @pytest.mark.asyncio
    async def test_disabled_returns_false(self, disabled_notifier):
        result = await disabled_notifier.send_alert("Test")
        assert result is False

    @pytest.mark.asyncio
    async def test_api_error_returns_false(self, notifier):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request"

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            result = await notifier.send_alert("Test")

        assert result is False

    @pytest.mark.asyncio
    async def test_timeout_returns_false(self, notifier):
        import httpx
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=httpx.TimeoutException("timeout")):
            result = await notifier.send_alert("Test")

        assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self, notifier):
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=Exception("network")):
            result = await notifier.send_alert("Test")

        assert result is False


class TestSendDailySummary:
    @pytest.mark.asyncio
    async def test_formats_stats(self, notifier):
        mock_response = MagicMock()
        mock_response.status_code = 200

        stats = {
            "posts_collected": 100,
            "posts_queued": 25,
            "drafts_created": 20,
            "drafts_approved": 15,
            "drafts_rejected": 3,
            "errors": 2,
            "targets_active": 5,
            "targets_degraded": 1,
        }

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
            result = await notifier.send_daily_summary(stats)

        assert result is True
        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
        assert "Daily Summary" in payload["text"]
        assert "100" in payload["text"]
        assert "25" in payload["text"]


class TestSendWeeklyReport:
    @pytest.mark.asyncio
    async def test_formats_weekly(self, notifier):
        mock_response = MagicMock()
        mock_response.status_code = 200

        stats = {
            "total_posts": 500,
            "total_drafts": 120,
            "approval_rate": 75.5,
            "edit_rate": 15.2,
            "reject_rate": 9.3,
            "best_target": "fb_group_x",
            "best_hour": "10:00",
            "ai_drafts": 30,
        }

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
            result = await notifier.send_weekly_report(stats)

        assert result is True
        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
        assert "Weekly Report" in payload["text"]
        assert "75.5%" in payload["text"]


class TestSpecificAlerts:
    @pytest.mark.asyncio
    async def test_block_detected(self, notifier):
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
            result = await notifier.notify_block_detected("target_1", "captcha")

        assert result is True
        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
        assert "Block Detected" in payload["text"]
        assert "target_1" in payload["text"]
        assert "captcha" in payload["text"]

    @pytest.mark.asyncio
    async def test_collection_error(self, notifier):
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
            result = await notifier.notify_collection_error("target_2", "timeout")

        assert result is True
        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
        assert "Collection Error" in payload["text"]
        assert "target_2" in payload["text"]

    @pytest.mark.asyncio
    async def test_service_health(self, notifier):
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
            result = await notifier.notify_service_health("api", "healthy")

        assert result is True
        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
        assert "✅" in payload["text"]
        assert "api" in payload["text"]

    @pytest.mark.asyncio
    async def test_service_health_unhealthy(self, notifier):
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
            result = await notifier.notify_service_health("worker", "unhealthy", "no heartbeat")

        assert result is True
        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
        assert "🚨" in payload["text"]
        assert "no heartbeat" in payload["text"]


class TestMessageFormat:
    @pytest.mark.asyncio
    async def test_uses_markdown_parse_mode(self, notifier):
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
            await notifier.send_alert("Test")

        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
        assert payload["parse_mode"] == "Markdown"
        assert payload["disable_web_page_preview"] is True

    @pytest.mark.asyncio
    async def test_correct_api_url(self, notifier):
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
            await notifier.send_alert("Test")

        url = mock_post.call_args.args[0] if mock_post.call_args.args else mock_post.call_args.kwargs.get("url", "")
        assert "test-token" in url
        assert "sendMessage" in url
