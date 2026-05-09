"""Tests for ``Notifier``.

Covers MarkdownV2 escaping, TTL-based dedup, and the retry loop for
transient 5xx/429 responses.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from bot.modules.notifier import Notifier, escape_markdown_v2


@pytest.fixture
def notifier():
    return Notifier(bot_token="test-token", chat_id="123")


class TestMarkdownEscape:
    def test_escapes_reserved_chars(self):
        assert escape_markdown_v2("target_id.v2") == r"target\_id\.v2"

    def test_handles_none(self):
        assert escape_markdown_v2(None) == ""

    def test_escapes_numeric_like(self):
        # Numbers stay, the period gets escaped.
        assert escape_markdown_v2(3.14) == r"3\.14"


class TestDedup:
    def test_same_key_suppressed(self, notifier):
        assert notifier._should_send("k1") is True
        assert notifier._should_send("k1") is False

    def test_different_keys_independent(self, notifier):
        assert notifier._should_send("a") is True
        assert notifier._should_send("b") is True

    def test_none_key_never_deduped(self, notifier):
        assert notifier._should_send(None) is True
        assert notifier._should_send(None) is True

    def test_clear_resets_dedup(self, notifier):
        notifier._should_send("k1")
        notifier.clear_dedup()
        assert notifier._should_send("k1") is True


class TestBlockAlertDedup:
    @pytest.mark.asyncio
    async def test_second_block_alert_for_same_target_is_suppressed(self, notifier):
        with patch.object(notifier, "_send_message", new=AsyncMock(return_value=True)) as send:
            first = await notifier.notify_block_detected("grp_1", "captcha")
            second = await notifier.notify_block_detected("grp_1", "captcha")
        assert first is True
        assert second is False
        send.assert_awaited_once()


class TestRetryLoop:
    @pytest.mark.asyncio
    async def test_retries_on_429_then_succeeds(self, notifier):
        notifier._max_retries = 2

        class FakeResp:
            def __init__(self, status_code: int, text: str = ""):
                self.status_code = status_code
                self.text = text

        async def _fake_post(self, url, json):  # noqa: ANN001
            if _fake_post.calls == 0:
                _fake_post.calls += 1
                return FakeResp(429, "too many")
            return FakeResp(200, "ok")

        _fake_post.calls = 0

        with patch("httpx.AsyncClient") as client_cls:
            instance = client_cls.return_value.__aenter__.return_value
            instance.post = AsyncMock(
                side_effect=[
                    type("R", (), {"status_code": 429, "text": "nope"})(),
                    type("R", (), {"status_code": 200, "text": "ok"})(),
                ]
            )
            # Speed up the retry sleep.
            with patch("bot.modules.notifier.asyncio.sleep", new=AsyncMock()):
                ok = await notifier._send_message("hello")
        assert ok is True

    @pytest.mark.asyncio
    async def test_gives_up_after_max_retries(self, notifier):
        notifier._max_retries = 2
        with patch("httpx.AsyncClient") as client_cls:
            instance = client_cls.return_value.__aenter__.return_value
            instance.post = AsyncMock(
                side_effect=[
                    type("R", (), {"status_code": 500, "text": "server"})(),
                    type("R", (), {"status_code": 500, "text": "server"})(),
                ]
            )
            with patch("bot.modules.notifier.asyncio.sleep", new=AsyncMock()):
                ok = await notifier._send_message("hello")
        assert ok is False
