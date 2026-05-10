"""Tests for CommentSender — Playwright comment posting with mocks.

Async mocks simulate Playwright's browser/context/page so we don't spin
up a real Chromium in CI. Real DOM smoke test runs via scripts/.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def fake_page():
    page = MagicMock()
    page.url = "https://www.facebook.com/photo/?fbid=x"
    page.goto = AsyncMock()
    page.wait_for_load_state = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.wait_for_selector = AsyncMock()
    page.query_selector = AsyncMock()
    page.evaluate = AsyncMock(return_value=None)
    page.keyboard = MagicMock()
    page.keyboard.type = AsyncMock()
    page.mouse = MagicMock()
    page.mouse.click = AsyncMock()
    return page


@pytest.fixture
def fake_context(fake_page):
    ctx = MagicMock()
    ctx.new_page = AsyncMock(return_value=fake_page)
    ctx.close = AsyncMock()
    return ctx


@pytest.fixture
def fake_browser(fake_context):
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=fake_context)
    browser.close = AsyncMock()
    return browser


@pytest.fixture
def fake_playwright(fake_browser, monkeypatch):
    """Patch async_playwright() → fake pw.chromium.launch returning browser."""
    pw_instance = MagicMock()
    pw_instance.chromium = MagicMock()
    pw_instance.chromium.launch = AsyncMock(return_value=fake_browser)

    class _PwCtx:
        async def __aenter__(self):
            return pw_instance

        async def __aexit__(self, *a):
            return None

    def _factory():
        return _PwCtx()

    import bot.modules.comment_sender as cs

    monkeypatch.setattr(cs, "async_playwright", _factory)
    return pw_instance


def _make_element(inner_text: str = "", aria_label: str = ""):
    el = MagicMock()
    el.click = AsyncMock()
    el.type = AsyncMock()
    el.press = AsyncMock()
    el.get_attribute = AsyncMock(return_value=aria_label)
    el.inner_text = AsyncMock(return_value=inner_text)
    el.focus = AsyncMock()
    return el


class TestImportsAndExceptions:
    def test_module_exports(self):
        from bot.modules.comment_sender import (
            CheckpointRequiredError,
            CommentSendError,
            CookieExpiredError,
            SendResult,
            send_comment,
        )

        assert CommentSendError is not None
        assert CheckpointRequiredError is not None
        assert CookieExpiredError is not None
        assert SendResult is not None
        assert callable(send_comment)


class TestSuccessFlow:
    @pytest.mark.asyncio
    async def test_types_text_and_clicks_post(
        self, fake_playwright, fake_page
    ):
        from bot.modules.comment_sender import send_comment

        textbox = _make_element(aria_label="Comment as Digi Markt")
        post_btn = _make_element(aria_label="Post comment")
        posted_comment = _make_element(
            aria_label="Comment by Digi Markt just now",
            inner_text="halo bro, mantap",
        )

        # Selector routing:
        #   textbox / post button / posted comment marker
        async def _query(selector, **kwargs):
            if "textbox" in selector or "contenteditable" in selector:
                return textbox
            if "Post comment" in selector:
                return post_btn
            if "Comment by" in selector:
                return posted_comment
            return None

        fake_page.query_selector.side_effect = _query
        fake_page.wait_for_selector.side_effect = _query

        result = await send_comment(
            post_url="https://www.facebook.com/photo/?fbid=x",
            comment_text="halo bro, mantap",
            cookies={"c_user": "123", "xs": "y"},
            display_name="Digi Markt",
        )

        assert result.success is True
        assert result.comment_text == "halo bro, mantap"
        # Should have typed text (either via keyboard.type or element.type)
        typed = (
            fake_page.keyboard.type.await_count
            + textbox.type.await_count
        )
        assert typed >= 1
        # Should have clicked Post comment
        assert post_btn.click.await_count == 1

    @pytest.mark.asyncio
    async def test_returns_success_result_shape(
        self, fake_playwright, fake_page
    ):
        from bot.modules.comment_sender import SendResult, send_comment

        textbox = _make_element(aria_label="Comment as Digi Markt")
        post_btn = _make_element(aria_label="Post comment")
        posted = _make_element(
            aria_label="Comment by Digi Markt just now", inner_text="hi"
        )

        async def _q(sel, **kw):
            if "textbox" in sel or "contenteditable" in sel:
                return textbox
            if "Post comment" in sel:
                return post_btn
            if "Comment by" in sel:
                return posted
            return None

        fake_page.query_selector.side_effect = _q
        fake_page.wait_for_selector.side_effect = _q

        result = await send_comment(
            post_url="https://x",
            comment_text="hi",
            cookies={"c_user": "1"},
            display_name="Digi Markt",
        )
        assert isinstance(result, SendResult)
        assert result.success is True
        assert result.error is None
        assert result.checkpoint is False


class TestCookieExpired:
    @pytest.mark.asyncio
    async def test_empty_cookies_raises(self, fake_playwright):
        from bot.modules.comment_sender import (
            CookieExpiredError,
            send_comment,
        )

        with pytest.raises(CookieExpiredError):
            await send_comment(
                post_url="https://x",
                comment_text="hi",
                cookies={},
                display_name="X",
            )

    @pytest.mark.asyncio
    async def test_missing_c_user_raises(self, fake_playwright):
        from bot.modules.comment_sender import (
            CookieExpiredError,
            send_comment,
        )

        with pytest.raises(CookieExpiredError):
            await send_comment(
                post_url="https://x",
                comment_text="hi",
                cookies={"xs": "y"},
                display_name="X",
            )

    @pytest.mark.asyncio
    async def test_login_redirect_raises(
        self, fake_playwright, fake_page
    ):
        from bot.modules.comment_sender import (
            CookieExpiredError,
            send_comment,
        )

        fake_page.url = "https://www.facebook.com/login"

        with pytest.raises(CookieExpiredError):
            await send_comment(
                post_url="https://x",
                comment_text="hi",
                cookies={"c_user": "1"},
                display_name="X",
            )


class TestCheckpoint:
    @pytest.mark.asyncio
    async def test_checkpoint_url_raises(
        self, fake_playwright, fake_page
    ):
        from bot.modules.comment_sender import (
            CheckpointRequiredError,
            send_comment,
        )

        fake_page.url = (
            "https://www.facebook.com/checkpoint/?next=/photo/%3Ffbid%3Dx"
        )

        with pytest.raises(CheckpointRequiredError):
            await send_comment(
                post_url="https://x",
                comment_text="hi",
                cookies={"c_user": "1"},
                display_name="X",
            )


class TestTextboxNotFound:
    @pytest.mark.asyncio
    async def test_no_textbox_returns_error(
        self, fake_playwright, fake_page
    ):
        from bot.modules.comment_sender import send_comment

        fake_page.query_selector.return_value = None
        fake_page.wait_for_selector.side_effect = Exception("timeout")

        result = await send_comment(
            post_url="https://x",
            comment_text="hi",
            cookies={"c_user": "1"},
            display_name="Digi Markt",
        )

        assert result.success is False
        assert result.error is not None
        assert (
            "comment" in result.error.lower()
            or "textbox" in result.error.lower()
            or "composer" in result.error.lower()
        )


class TestInputValidation:
    @pytest.mark.asyncio
    async def test_empty_comment_raises(self, fake_playwright):
        from bot.modules.comment_sender import (
            CommentSendError,
            send_comment,
        )

        with pytest.raises(CommentSendError):
            await send_comment(
                post_url="https://x",
                comment_text="   ",
                cookies={"c_user": "1"},
                display_name="X",
            )

    @pytest.mark.asyncio
    async def test_missing_post_url_raises(self, fake_playwright):
        from bot.modules.comment_sender import (
            CommentSendError,
            send_comment,
        )

        with pytest.raises(CommentSendError):
            await send_comment(
                post_url="",
                comment_text="hi",
                cookies={"c_user": "1"},
                display_name="X",
            )


class TestPerCharDelay:
    @pytest.mark.asyncio
    async def test_respects_delay_range_param(
        self, fake_playwright, fake_page
    ):
        """Passing delay_range=(5, 15) should use those bounds."""
        from bot.modules.comment_sender import send_comment

        textbox = _make_element(aria_label="Comment as D")
        post_btn = _make_element(aria_label="Post comment")
        posted = _make_element(
            aria_label="Comment by D just now", inner_text="hi"
        )

        async def _q(sel, **kw):
            if "textbox" in sel or "contenteditable" in sel:
                return textbox
            if "Post comment" in sel:
                return post_btn
            if "Comment by" in sel:
                return posted
            return None

        fake_page.query_selector.side_effect = _q
        fake_page.wait_for_selector.side_effect = _q

        # Should not raise; delay_range accepted.
        result = await send_comment(
            post_url="https://x",
            comment_text="hi",
            cookies={"c_user": "1"},
            display_name="D",
            delay_range_ms=(5, 15),
        )
        assert result.success is True
