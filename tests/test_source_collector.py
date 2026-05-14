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
    context.add_init_script = AsyncMock()
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

    async def test_login_wall_dom_raises_even_when_url_safe(
        self, mock_playwright_stack
    ):
        """FB serves login wall without URL redirect when cookies stale.

        URL stays on ``facebook.com/home.php`` but body renders account
        chooser. DOM probe must catch this and raise CookieExpiredError.
        """
        mocks = mock_playwright_stack
        mocks["page"].url = "https://www.facebook.com/home.php"
        # Route evaluate: login-wall probe returns marker; other evals
        # (hydrate / extract) return empty list so the loop no-ops.
        def _eval(script, *args, **kwargs):
            if "loginMarker" in (script or ""):
                return {"loginMarker": True, "reason": "text:Masuk Facebook"}
            return []
        mocks["page"].evaluate.side_effect = _eval

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

    async def test_viewport_kwarg_forwarded_to_new_context(
        self, mock_playwright_stack
    ):
        """Phase I-A-3 — pinned viewport must reach browser.new_context.

        Caller (orchestrator) passes ``viewport={"width", "height"}`` from
        ``FBAccountService.ensure_fingerprint``. Without this, every scan
        re-rolls a viewport and FB sees fingerprint drift.
        """
        mocks = mock_playwright_stack
        src = {"id": 1, "type": "home_feed"}

        with patch(
            "bot.modules.source_collector.async_playwright",
            mocks["async_playwright"],
        ):
            await scan_source(
                src,
                {"c_user": "1"},
                user_agent="UA-PIN",
                viewport={"width": 1440, "height": 900},
            )

        kwargs = mocks["browser"].new_context.await_args.kwargs
        assert kwargs["user_agent"] == "UA-PIN"
        assert kwargs["viewport"] == {"width": 1440, "height": 900}

    async def test_on_cookies_refresh_invoked_with_captured_cookies(
        self, mock_playwright_stack
    ):
        """Phase I-B-3 — on success, harvest rotated cookies and pass to callback.

        FB rotates ``xs`` mid-session. Scanner must capture the current
        cookie state from the BrowserContext right before the context
        closes, so the caller (orchestrator) can persist it back to the
        DB via ``FBAccountService.refresh_cookies_silent``.
        """
        mocks = mock_playwright_stack
        # Fake FB rotating xs during the session.
        mocks["context"].cookies = AsyncMock(
            return_value=[
                {"name": "c_user", "value": "1", "domain": ".facebook.com"},
                {"name": "xs", "value": "ROTATED", "domain": ".facebook.com"},
            ]
        )

        seen: dict[str, str] = {}

        async def _on_refresh(new_cookies):
            seen.update(new_cookies)

        src = {"id": 1, "type": "home_feed"}
        with patch(
            "bot.modules.source_collector.async_playwright",
            mocks["async_playwright"],
        ):
            result = await scan_source(
                src, {"c_user": "1"}, on_cookies_refresh=_on_refresh
            )

        assert result.success is True
        assert seen == {"c_user": "1", "xs": "ROTATED"}

    async def test_on_cookies_refresh_skipped_on_cookie_expired(
        self, mock_playwright_stack
    ):
        """Don't persist captured cookies when session was invalid anyway."""
        mocks = mock_playwright_stack
        mocks["page"].url = "https://www.facebook.com/login/"

        called = False

        async def _on_refresh(_):
            nonlocal called
            called = True

        src = {"id": 1, "type": "home_feed"}
        with patch(
            "bot.modules.source_collector.async_playwright",
            mocks["async_playwright"],
        ):
            with pytest.raises(CookieExpiredError):
                await scan_source(
                    src, {"c_user": "1"}, on_cookies_refresh=_on_refresh
                )

        assert called is False

    async def test_on_cookies_refresh_tolerates_callback_exception(
        self, mock_playwright_stack
    ):
        """Callback crash must not fail the scan — refresh is best-effort."""
        mocks = mock_playwright_stack
        mocks["context"].cookies = AsyncMock(
            return_value=[
                {"name": "c_user", "value": "1", "domain": ".facebook.com"},
            ]
        )

        async def _on_refresh(_):
            raise RuntimeError("db write blew up")

        src = {"id": 1, "type": "home_feed"}
        with patch(
            "bot.modules.source_collector.async_playwright",
            mocks["async_playwright"],
        ):
            result = await scan_source(
                src, {"c_user": "1"}, on_cookies_refresh=_on_refresh
            )

        # Scan itself still reports success — the harvest is opportunistic.
        assert result.success is True


# --- Phase I-C-3 — persistent profile routing -----------------------------


@pytest.mark.asyncio
class TestScanSourcePersistentRouting:
    """When ``FB_USE_PERSISTENT_PROFILE=1`` and ``account_id`` is given,
    ``scan_source`` must route through ``create_persistent_session``
    instead of ``browser.launch`` + ``create_session_context``.

    The default (env unset) keeps the legacy path so we have a single-flag
    rollback if persistent profile causes regressions in production.
    """

    async def test_persistent_route_when_env_set(
        self, mock_playwright_stack, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("FB_USE_PERSISTENT_PROFILE", "1")
        monkeypatch.setenv("FB_PROFILE_ROOT", str(tmp_path))

        mocks = mock_playwright_stack

        # Capture create_persistent_session calls.
        seen: dict = {}

        async def _fake_persistent(pw, **kwargs):
            seen["called"] = True
            seen["kwargs"] = kwargs
            return mocks["context"]

        with patch(
            "bot.modules.source_collector.async_playwright",
            mocks["async_playwright"],
        ), patch(
            "bot.modules.source_collector.create_persistent_session",
            _fake_persistent,
        ):
            await scan_source(
                {"id": 1, "type": "home_feed"},
                {"c_user": "1"},
                account_id=42,
                user_agent="UA-PIN",
                viewport={"width": 1366, "height": 768},
            )

        assert seen.get("called") is True
        assert seen["kwargs"]["account_id"] == 42
        assert seen["kwargs"]["user_agent"] == "UA-PIN"
        assert seen["kwargs"]["viewport"] == {"width": 1366, "height": 768}
        # Legacy browser.launch must NOT have been called when routing
        # through persistent profile.
        assert mocks["pw"].chromium.launch.await_count == 0

    async def test_legacy_route_when_env_unset(
        self, mock_playwright_stack, monkeypatch
    ):
        """Default (env unset) keeps the existing browser.launch path."""
        monkeypatch.delenv("FB_USE_PERSISTENT_PROFILE", raising=False)

        mocks = mock_playwright_stack

        with patch(
            "bot.modules.source_collector.async_playwright",
            mocks["async_playwright"],
        ):
            await scan_source(
                {"id": 1, "type": "home_feed"},
                {"c_user": "1"},
                account_id=42,
            )

        # Legacy: chromium.launch + new_context route.
        assert mocks["pw"].chromium.launch.await_count == 1

    async def test_legacy_route_when_account_id_missing(
        self, mock_playwright_stack, monkeypatch
    ):
        """Even with env set, no ``account_id`` → fallback (defensive)."""
        monkeypatch.setenv("FB_USE_PERSISTENT_PROFILE", "1")

        mocks = mock_playwright_stack

        with patch(
            "bot.modules.source_collector.async_playwright",
            mocks["async_playwright"],
        ):
            await scan_source(
                {"id": 1, "type": "home_feed"},
                {"c_user": "1"},
                # account_id intentionally omitted.
            )

        assert mocks["pw"].chromium.launch.await_count == 1
