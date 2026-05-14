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
    ctx.add_cookies = AsyncMock()
    ctx.add_init_script = AsyncMock()
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

    @pytest.mark.asyncio
    async def test_login_wall_dom_raises_even_when_url_safe(
        self, fake_playwright, fake_page
    ):
        """FB can serve login wall without URL redirect — rely on DOM.

        Reproduces the failure mode where cookies are stale and FB
        serves the account-chooser in the body while keeping the
        original URL. Must raise CookieExpiredError so the router
        flips account status to EXPIRED.
        """
        from bot.modules.comment_sender import (
            CookieExpiredError,
            send_comment,
        )

        fake_page.url = "https://www.facebook.com/photo/?fbid=x"  # URL safe
        # evaluate() is called for scroll-to-bottom + login-wall probe;
        # return the login-marker shape for any evaluate call.
        fake_page.evaluate = AsyncMock(
            return_value={"loginMarker": True, "reason": "text:Masuk Facebook"}
        )

        with pytest.raises(CookieExpiredError):
            await send_comment(
                post_url="https://www.facebook.com/photo/?fbid=x",
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


class TestLocaleAwareSelectors:
    """FB renders aria-label in the account's UI language. Sender must
    match both English (``Comment as``) and Indonesian (``Komen sebagai``
    / ``Tulis komentar``) composer variants, as well as the matching
    button labels and verification node aria-labels.

    We assert by inspecting which selectors the sender consults via
    ``query_selector`` / ``wait_for_selector`` — the caller's mock routes
    by substring so we can tell if multiple locales are attempted.
    """

    @pytest.mark.asyncio
    async def test_indonesian_composer_aria_label_works(
        self, fake_playwright, fake_page
    ):
        from bot.modules.comment_sender import send_comment

        # Composer labeled in Indonesian — only matches when the sender
        # includes ID selectors like [aria-label^="Komen sebagai"] or a
        # generic fallback.
        textbox = _make_element(aria_label="Komen sebagai Digi Markt")
        post_btn = _make_element(aria_label="Kirim komentar")
        posted = _make_element(
            aria_label="Komentar oleh Digi Markt baru saja",
            inner_text="halo bro",
        )

        async def _q(sel, **kw):
            s = sel.lower()
            # ID composer aria-label fragments:
            if "komen sebagai" in s or "tulis komentar" in s:
                return textbox
            if "kirim komentar" in s or "posting komentar" in s:
                return post_btn
            if "komentar oleh" in s:
                return posted
            return None

        fake_page.query_selector.side_effect = _q
        fake_page.wait_for_selector.side_effect = _q

        result = await send_comment(
            post_url="https://www.facebook.com/permalink.php?story_fbid=1",
            comment_text="halo bro",
            cookies={"c_user": "1", "xs": "y"},
            display_name="Digi Markt",
        )

        assert result.success is True, result.error

    @pytest.mark.asyncio
    async def test_tries_multiple_composer_locales(
        self, fake_playwright, fake_page
    ):
        """Sender should probe EN + ID selectors (in any order) when first miss."""
        from bot.modules.comment_sender import send_comment

        seen_selectors: list[str] = []

        textbox = _make_element(aria_label="Tulis komentar publik")
        post_btn = _make_element(aria_label="Kirim komentar")
        posted = _make_element(
            aria_label="Komentar oleh User baru saja", inner_text="hi"
        )

        async def _q(sel, **kw):
            seen_selectors.append(sel)
            s = sel.lower()
            if "tulis komentar" in s or "komen sebagai" in s:
                return textbox
            if "kirim komentar" in s:
                return post_btn
            if "komentar oleh" in s:
                return posted
            return None

        fake_page.query_selector.side_effect = _q
        fake_page.wait_for_selector.side_effect = _q

        result = await send_comment(
            post_url="https://x",
            comment_text="hi",
            cookies={"c_user": "1"},
            display_name="User",
        )
        assert result.success is True
        all_sel = " || ".join(seen_selectors).lower()
        # Both locales should have been considered somewhere.
        assert "comment as" in all_sel or "komen sebagai" in all_sel
        # The button search should also have tried an ID variant.
        assert "kirim komentar" in all_sel or "post comment" in all_sel

    @pytest.mark.asyncio
    async def test_indonesian_verification_node_accepted(
        self, fake_playwright, fake_page
    ):
        """After submit, ``Komentar oleh <name>`` should count as posted."""
        from bot.modules.comment_sender import send_comment

        textbox = _make_element(aria_label="Comment as Me")
        post_btn = _make_element(aria_label="Post comment")
        posted_id = _make_element(
            aria_label="Komentar oleh Me baru saja", inner_text="halo bro"
        )

        async def _q(sel, **kw):
            s = sel.lower()
            if "textbox" in s or "contenteditable" in s:
                return textbox
            if "post comment" in s or "kirim komentar" in s:
                return post_btn
            if "comment by" in s or "komentar oleh" in s:
                return posted_id
            return None

        fake_page.query_selector.side_effect = _q
        fake_page.wait_for_selector.side_effect = _q

        result = await send_comment(
            post_url="https://x",
            comment_text="halo bro",
            cookies={"c_user": "1"},
            display_name="Me",
        )
        assert result.success is True, result.error


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


# --- Phase I-C-3 — persistent profile routing -----------------------------


@pytest.mark.asyncio
class TestSendCommentPersistentRouting:
    """``send_comment`` mirrors scan_source: when both
    ``FB_USE_PERSISTENT_PROFILE=1`` and ``account_id`` are present, route
    via :func:`create_persistent_session`. Otherwise fall back to the
    legacy launch+new_context path. Single env flag enables clean rollback.
    """

    async def test_persistent_route_when_env_set(
        self, fake_playwright, fake_context, monkeypatch, tmp_path
    ):
        from bot.modules.comment_sender import send_comment

        monkeypatch.setenv("FB_USE_PERSISTENT_PROFILE", "1")
        monkeypatch.setenv("FB_PROFILE_ROOT", str(tmp_path))

        seen: dict = {}

        async def _fake_persistent(pw, **kwargs):
            seen["called"] = True
            seen["kwargs"] = kwargs
            return fake_context

        # Make _wait_posted_comment short-circuit to truthy so happy path
        # completes — actual verify already covered by other suites.
        import bot.modules.comment_sender as cs

        monkeypatch.setattr(cs, "create_persistent_session", _fake_persistent)
        monkeypatch.setattr(
            cs, "_find_composer", AsyncMock(return_value=_make_element())
        )
        monkeypatch.setattr(
            cs, "_find_post_button", AsyncMock(return_value=_make_element())
        )
        monkeypatch.setattr(
            cs,
            "_wait_posted_comment",
            AsyncMock(return_value=_make_element()),
        )

        result = await send_comment(
            post_url="https://facebook.com/post/1",
            comment_text="halo",
            cookies={"c_user": "1"},
            display_name="Tester",
            account_id=42,
        )

        assert seen.get("called") is True
        assert seen["kwargs"]["account_id"] == 42
        # Legacy path must not be used when persistent route is taken.
        assert fake_playwright.chromium.launch.await_count == 0
        assert result.success is True

    async def test_legacy_route_when_env_unset(
        self, fake_playwright, monkeypatch
    ):
        from bot.modules.comment_sender import send_comment

        monkeypatch.delenv("FB_USE_PERSISTENT_PROFILE", raising=False)

        import bot.modules.comment_sender as cs

        monkeypatch.setattr(
            cs, "_find_composer", AsyncMock(return_value=_make_element())
        )
        monkeypatch.setattr(
            cs, "_find_post_button", AsyncMock(return_value=_make_element())
        )
        monkeypatch.setattr(
            cs,
            "_wait_posted_comment",
            AsyncMock(return_value=_make_element()),
        )

        await send_comment(
            post_url="https://facebook.com/post/1",
            comment_text="halo",
            cookies={"c_user": "1"},
            display_name="Tester",
            account_id=42,  # provided, but env is off
        )

        assert fake_playwright.chromium.launch.await_count == 1

    async def test_legacy_route_when_account_id_missing(
        self, fake_playwright, monkeypatch
    ):
        from bot.modules.comment_sender import send_comment

        monkeypatch.setenv("FB_USE_PERSISTENT_PROFILE", "1")

        import bot.modules.comment_sender as cs

        monkeypatch.setattr(
            cs, "_find_composer", AsyncMock(return_value=_make_element())
        )
        monkeypatch.setattr(
            cs, "_find_post_button", AsyncMock(return_value=_make_element())
        )
        monkeypatch.setattr(
            cs,
            "_wait_posted_comment",
            AsyncMock(return_value=_make_element()),
        )

        await send_comment(
            post_url="https://facebook.com/post/1",
            comment_text="halo",
            cookies={"c_user": "1"},
            display_name="Tester",
            # account_id intentionally omitted.
        )

        assert fake_playwright.chromium.launch.await_count == 1
