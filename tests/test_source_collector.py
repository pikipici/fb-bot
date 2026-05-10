"""Tests for source_collector — source-aware Playwright scraper."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.modules.source_collector import (
    SOURCE_SCROLL_COUNT,
    CookieExpiredError,
    SourceCollectorResult,
    build_source_url,
    scan_source,
)


# --- build_source_url -----------------------------------------------------


class TestBuildSourceUrl:
    def test_home_feed_points_to_home_php(self):
        # /?sk=h_chr never hydrates in headless chromium. /home.php renders
        # the same virtualized feed but hydrates reliably.
        src = {"type": "home_feed"}
        assert build_source_url(src) == "https://www.facebook.com/home.php"

    def test_group_uses_fb_entity_id(self):
        src = {"type": "group", "fb_entity_id": "123456"}
        assert (
            build_source_url(src)
            == "https://www.facebook.com/groups/123456"
        )

    def test_group_falls_back_to_url_if_no_entity_id(self):
        src = {
            "type": "group",
            "fb_entity_id": None,
            "url": "https://www.facebook.com/groups/jual-beli-jkt",
        }
        assert (
            build_source_url(src)
            == "https://www.facebook.com/groups/jual-beli-jkt"
        )

    def test_page_uses_fb_entity_id_with_posts_suffix(self):
        src = {"type": "page", "fb_entity_id": "technews"}
        assert (
            build_source_url(src)
            == "https://www.facebook.com/technews/posts"
        )

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError):
            build_source_url({"type": "profile"})


# --- scan_source ---------------------------------------------------------


@pytest.fixture
def mock_playwright_stack():
    """Build an async-compatible playwright stack.

    Returns (patcher, mocks_dict) so tests can inspect calls.
    """
    page = MagicMock()
    page.goto = AsyncMock()
    page.content = AsyncMock(return_value="<html></html>")
    page.evaluate = AsyncMock(return_value=[])
    page.wait_for_timeout = AsyncMock()
    page.url = "https://www.facebook.com/"

    context = MagicMock()
    context.new_page = AsyncMock(return_value=page)
    context.add_cookies = AsyncMock()
    context.close = AsyncMock()

    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)
    browser.close = AsyncMock()

    pw = MagicMock()
    pw.chromium.launch = AsyncMock(return_value=browser)

    async_pw_cm = MagicMock()
    async_pw_cm.__aenter__ = AsyncMock(return_value=pw)
    async_pw_cm.__aexit__ = AsyncMock(return_value=None)

    return {
        "async_playwright": MagicMock(return_value=async_pw_cm),
        "pw": pw,
        "browser": browser,
        "context": context,
        "page": page,
    }


@pytest.mark.asyncio
class TestScanSource:
    async def test_returns_result_with_posts(self, mock_playwright_stack):
        mocks = mock_playwright_stack
        mocks["page"].evaluate.return_value = [
            {
                "fb_post_id": "abc123",
                "author_name": "Tester",
                "text": "Post content here",
                "likes": 10,
                "comments": 2,
                "shares": 1,
                "post_url": "https://fb.com/abc123",
                "thumbnail_url": None,
            }
        ]
        src = {"id": 1, "type": "home_feed"}
        cookies = {"c_user": "1"}

        with patch(
            "bot.modules.source_collector.async_playwright",
            mocks["async_playwright"],
        ):
            result = await scan_source(src, cookies)

        assert isinstance(result, SourceCollectorResult)
        assert result.success is True
        assert len(result.posts) == 1
        assert result.posts[0]["fb_post_id"] == "abc123"
        assert result.posts[0]["reactions_total"] == 13  # 10+2+1

    async def test_goes_to_correct_url_per_type(self, mock_playwright_stack):
        mocks = mock_playwright_stack
        src = {"id": 1, "type": "group", "fb_entity_id": "999"}

        with patch(
            "bot.modules.source_collector.async_playwright",
            mocks["async_playwright"],
        ):
            await scan_source(src, {"c_user": "1"})

        args = mocks["page"].goto.call_args
        assert args.args[0] == "https://www.facebook.com/groups/999"

    async def test_scrolls_configured_times(self, mock_playwright_stack):
        mocks = mock_playwright_stack
        src = {"id": 1, "type": "home_feed"}

        with patch(
            "bot.modules.source_collector.async_playwright",
            mocks["async_playwright"],
        ):
            await scan_source(src, {"c_user": "1"})

        # ``page.evaluate`` is called once per scroll iteration for the
        # extraction script.
        assert mocks["page"].evaluate.call_count >= SOURCE_SCROLL_COUNT

    async def test_deduplicates_posts_across_scrolls(self, mock_playwright_stack):
        mocks = mock_playwright_stack
        dup = {
            "fb_post_id": "same1",
            "author_name": "A",
            "text": "hi",
            "likes": 1,
            "comments": 0,
            "shares": 0,
            "post_url": "",
            "thumbnail_url": None,
        }
        mocks["page"].evaluate.return_value = [dup]

        src = {"id": 1, "type": "home_feed"}
        with patch(
            "bot.modules.source_collector.async_playwright",
            mocks["async_playwright"],
        ):
            result = await scan_source(src, {"c_user": "1"})

        assert len(result.posts) == 1

    async def test_empty_cookies_raises_expired(self, mock_playwright_stack):
        """Missing c_user is a configuration bug — don't even launch."""
        src = {"id": 1, "type": "home_feed"}
        with pytest.raises(CookieExpiredError):
            await scan_source(src, {})

    async def test_redirect_to_login_raises_expired(self, mock_playwright_stack):
        mocks = mock_playwright_stack
        mocks["page"].url = "https://www.facebook.com/login/"

        src = {"id": 1, "type": "home_feed"}
        with patch(
            "bot.modules.source_collector.async_playwright",
            mocks["async_playwright"],
        ):
            with pytest.raises(CookieExpiredError):
                await scan_source(src, {"c_user": "1"})

    async def test_closes_browser_on_success(self, mock_playwright_stack):
        mocks = mock_playwright_stack
        src = {"id": 1, "type": "home_feed"}

        with patch(
            "bot.modules.source_collector.async_playwright",
            mocks["async_playwright"],
        ):
            await scan_source(src, {"c_user": "1"})

        mocks["browser"].close.assert_awaited()

    async def test_closes_browser_on_error(self, mock_playwright_stack):
        mocks = mock_playwright_stack
        mocks["page"].goto.side_effect = RuntimeError("boom")
        src = {"id": 1, "type": "home_feed"}

        with patch(
            "bot.modules.source_collector.async_playwright",
            mocks["async_playwright"],
        ):
            result = await scan_source(src, {"c_user": "1"})

        assert result.success is False
        assert "boom" in result.error
        mocks["browser"].close.assert_awaited()
