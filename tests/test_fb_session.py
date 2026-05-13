"""Tests for fb_session — Playwright cookie injection helper."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.modules.fb_session import (
    DEFAULT_USER_AGENT,
    STEALTH_INIT_SCRIPT,
    capture_cookies_from_context,
    cookies_dict_to_playwright_format,
    create_session_context,
)


# --- cookies_dict_to_playwright_format -----------------------------------


class TestCookiesFormat:
    def test_converts_flat_dict_to_playwright_cookie_list(self):
        cookies = {"c_user": "123", "xs": "abc"}
        result = cookies_dict_to_playwright_format(cookies)
        assert isinstance(result, list)
        assert len(result) == 2
        for item in result:
            assert item["domain"] == ".facebook.com"
            assert item["path"] == "/"
            assert item["secure"] is True
            assert item["httpOnly"] is False
            assert item["sameSite"] in ("Lax", "None", "Strict")
            assert "name" in item and "value" in item

    def test_preserves_values(self):
        cookies = {"c_user": "61577777450562", "xs": "abc=def"}
        result = cookies_dict_to_playwright_format(cookies)
        by_name = {c["name"]: c["value"] for c in result}
        assert by_name["c_user"] == "61577777450562"
        assert by_name["xs"] == "abc=def"

    def test_empty_dict_returns_empty_list(self):
        assert cookies_dict_to_playwright_format({}) == []

    def test_custom_domain(self):
        cookies = {"c_user": "1"}
        result = cookies_dict_to_playwright_format(
            cookies, domain=".m.facebook.com"
        )
        assert result[0]["domain"] == ".m.facebook.com"


# --- create_session_context ----------------------------------------------


@pytest.mark.asyncio
class TestCreateSessionContext:
    async def test_injects_cookies_into_new_context(self):
        """Playwright: browser.new_context() -> context.add_cookies(list)."""
        cookies = {"c_user": "123", "xs": "abc"}

        mock_context = MagicMock()
        mock_context.add_cookies = AsyncMock()
        mock_browser = MagicMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        result = await create_session_context(mock_browser, cookies)

        assert result is mock_context
        mock_browser.new_context.assert_called_once()
        kwargs = mock_browser.new_context.call_args.kwargs
        # UA must be set so FB accepts the session.
        assert "user_agent" in kwargs
        assert kwargs["user_agent"]

        mock_context.add_cookies.assert_called_once()
        injected = mock_context.add_cookies.call_args.args[0]
        by_name = {c["name"]: c["value"] for c in injected}
        assert by_name == {"c_user": "123", "xs": "abc"}

    async def test_uses_default_user_agent_when_none_given(self):
        mock_context = MagicMock()
        mock_context.add_cookies = AsyncMock()
        mock_browser = MagicMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        await create_session_context(mock_browser, {"c_user": "1"})

        kwargs = mock_browser.new_context.call_args.kwargs
        assert kwargs["user_agent"] == DEFAULT_USER_AGENT

    async def test_accepts_custom_user_agent(self):
        mock_context = MagicMock()
        mock_context.add_cookies = AsyncMock()
        mock_browser = MagicMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        custom_ua = "Custom/1.0"
        await create_session_context(
            mock_browser, {"c_user": "1"}, user_agent=custom_ua
        )

        kwargs = mock_browser.new_context.call_args.kwargs
        assert kwargs["user_agent"] == custom_ua

    async def test_sets_realistic_viewport(self):
        mock_context = MagicMock()
        mock_context.add_cookies = AsyncMock()
        mock_browser = MagicMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        await create_session_context(mock_browser, {"c_user": "1"})

        kwargs = mock_browser.new_context.call_args.kwargs
        assert "viewport" in kwargs
        viewport = kwargs["viewport"]
        assert "width" in viewport and "height" in viewport
        # realistic desktop resolution range
        assert 1280 <= viewport["width"] <= 1920
        assert 720 <= viewport["height"] <= 1080

    async def test_empty_cookies_still_creates_context(self):
        """Empty dict is allowed — caller checks cookie validity separately."""
        mock_context = MagicMock()
        mock_context.add_cookies = AsyncMock()
        mock_browser = MagicMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        result = await create_session_context(mock_browser, {})

        assert result is mock_context
        # add_cookies should still be called, but with empty list
        mock_context.add_cookies.assert_called_once_with([])

    async def test_locale_and_timezone_set_for_id(self):
        """Indonesia locale + timezone so FB doesn't flag the session."""
        mock_context = MagicMock()
        mock_context.add_cookies = AsyncMock()
        mock_browser = MagicMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        await create_session_context(mock_browser, {"c_user": "1"})

        kwargs = mock_browser.new_context.call_args.kwargs
        assert kwargs.get("locale", "").startswith("id")
        assert kwargs.get("timezone_id") == "Asia/Jakarta"


# --- capture_cookies_from_context (Phase I-B-1) ---------------------------


@pytest.mark.asyncio
class TestCaptureCookiesFromContext:
    """Phase I-B-1 — after a successful scan/send we must harvest the
    current cookie state from the live BrowserContext so any rotated
    session cookies (e.g. FB rotating ``xs`` mid-session) get persisted
    back. Without this the DB row goes stale and the next tick uses the
    pre-rotation cookie that FB has already invalidated.
    """

    async def test_flattens_context_cookies_to_dict(self):
        ctx = MagicMock()
        ctx.cookies = AsyncMock(
            return_value=[
                {
                    "name": "c_user",
                    "value": "12345",
                    "domain": ".facebook.com",
                },
                {"name": "xs", "value": "abc|def", "domain": ".facebook.com"},
                {"name": "datr", "value": "D", "domain": ".facebook.com"},
            ]
        )

        out = await capture_cookies_from_context(ctx)

        assert out == {"c_user": "12345", "xs": "abc|def", "datr": "D"}
        ctx.cookies.assert_awaited_once()

    async def test_filters_non_facebook_domains(self):
        ctx = MagicMock()
        ctx.cookies = AsyncMock(
            return_value=[
                {"name": "c_user", "value": "1", "domain": ".facebook.com"},
                {"name": "junk", "value": "x", "domain": ".otherdomain.com"},
                {"name": "ad_id", "value": "y", "domain": ".google.com"},
            ]
        )

        out = await capture_cookies_from_context(ctx)

        assert out == {"c_user": "1"}

    async def test_keeps_m_and_www_subdomains(self):
        """m.facebook.com and www.facebook.com cookies must be preserved."""
        ctx = MagicMock()
        ctx.cookies = AsyncMock(
            return_value=[
                {"name": "c_user", "value": "1", "domain": ".facebook.com"},
                {
                    "name": "mobile_flag",
                    "value": "m",
                    "domain": "m.facebook.com",
                },
                {
                    "name": "web_flag",
                    "value": "w",
                    "domain": "www.facebook.com",
                },
            ]
        )

        out = await capture_cookies_from_context(ctx)

        assert out == {
            "c_user": "1",
            "mobile_flag": "m",
            "web_flag": "w",
        }

    async def test_empty_cookie_list_returns_empty_dict(self):
        ctx = MagicMock()
        ctx.cookies = AsyncMock(return_value=[])
        assert await capture_cookies_from_context(ctx) == {}

    async def test_none_return_tolerated(self):
        """Some playwright mocks return None; don't crash."""
        ctx = MagicMock()
        ctx.cookies = AsyncMock(return_value=None)
        assert await capture_cookies_from_context(ctx) == {}


# --- STEALTH_INIT_SCRIPT + add_init_script wiring (Phase I-E) -------------


class TestStealthInitScriptConstant:
    """Phase I-E-1 — the stealth init script is a const we inject into every
    fresh BrowserContext via ``context.add_init_script``. Keep the patch
    minimal on purpose (YAGNI — no full playwright-stealth yet). The const
    must cover the three cheapest/highest-signal tells Facebook's anti-bot
    reads on every page load.
    """

    def test_overrides_navigator_webdriver(self):
        assert "navigator" in STEALTH_INIT_SCRIPT
        assert "webdriver" in STEALTH_INIT_SCRIPT
        # Must replace the getter so `navigator.webdriver` evaluates falsy.
        assert "=> false" in STEALTH_INIT_SCRIPT or "=>false" in STEALTH_INIT_SCRIPT

    def test_overrides_navigator_plugins_non_empty(self):
        """Headless Chromium reports ``navigator.plugins.length === 0`` —
        a dead giveaway. Patch must install at least one fake plugin entry.
        """
        assert "plugins" in STEALTH_INIT_SCRIPT

    def test_overrides_navigator_languages_indonesia_first(self):
        """Session impersonates an Indonesian user — ``navigator.languages``
        should lead with ``id`` so it matches the locale we set on the
        context.
        """
        assert "languages" in STEALTH_INIT_SCRIPT
        assert "id-ID" in STEALTH_INIT_SCRIPT or "'id'" in STEALTH_INIT_SCRIPT

    def test_shims_window_chrome(self):
        """``window.chrome`` is undefined under headless by default. Real
        Chrome always has ``window.chrome`` populated. Install a minimal
        shim so feature-detects pass.
        """
        assert "window.chrome" in STEALTH_INIT_SCRIPT


@pytest.mark.asyncio
class TestCreateSessionContextInjectsStealth:
    """Phase I-E-1 — every context created through ``create_session_context``
    must have the stealth patch registered via ``add_init_script`` BEFORE
    the first navigation, so FB anti-bot sees the patched navigator from
    the very first page load. Order matters: add_init_script must be
    called on the freshly-created context (we don't care strictly whether
    it's before or after ``add_cookies``, but it must happen before the
    context is handed back to the caller).
    """

    async def test_registers_stealth_init_script_on_context(self):
        mock_context = MagicMock()
        mock_context.add_cookies = AsyncMock()
        mock_context.add_init_script = AsyncMock()
        mock_browser = MagicMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        result = await create_session_context(mock_browser, {"c_user": "1"})

        assert result is mock_context
        mock_context.add_init_script.assert_awaited_once()
        script_arg = mock_context.add_init_script.call_args.args[0]
        assert script_arg == STEALTH_INIT_SCRIPT

    async def test_stealth_script_registered_before_context_returned(self):
        """Guard against a regression where someone wires the patch at
        page-level instead of context-level — every page opened from this
        context inherits the init script, which is what we want.
        """
        calls: list[str] = []

        mock_context = MagicMock()

        async def _add_cookies(_payload):
            calls.append("add_cookies")

        async def _add_init(_script):
            calls.append("add_init_script")

        mock_context.add_cookies = AsyncMock(side_effect=_add_cookies)
        mock_context.add_init_script = AsyncMock(side_effect=_add_init)

        mock_browser = MagicMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        await create_session_context(mock_browser, {"c_user": "1"})

        # Both must have been called on the returned context.
        assert "add_init_script" in calls
        assert "add_cookies" in calls
