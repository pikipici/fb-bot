"""Comment Sender — Playwright-driven FB comment posting (Layer 2).

Workflow::

    send_comment(
        post_url="https://www.facebook.com/photo/?fbid=...",
        comment_text="halo bro, mantap",
        cookies={"c_user": "...", "xs": "...", ...},
        display_name="Digi Markt",
    )

1. Launch headless Chromium via :mod:`playwright.async_api`.
2. Inject cookies via :func:`bot.modules.fb_session.create_session_context`.
3. Navigate to ``post_url``. Detect checkpoint/login redirects early.
4. Find the comment composer:
   ``div[contenteditable="true"][role="textbox"][aria-label^="Comment as"]``
   If not present (e.g. post opened in dialog), click the
   ``div[role="button"][aria-label="Leave a comment"]`` button first.
5. Focus composer → type per-char with humanlike delay (50-150ms default).
6. Click ``div[role="button"][aria-label="Post comment"]``.
7. Verify the posted comment by waiting for a
   ``div[role="article"][aria-label^="Comment by <display_name>"]`` node
   with matching ``inner_text`` prefix.

Returns :class:`SendResult` (non-raising happy/failure) but raises
:class:`CookieExpiredError` / :class:`CheckpointRequiredError` /
:class:`CommentSendError` for hard pre-flight failures.

Rate-limiting is NOT handled here — the caller (F5 router / send wiring)
must invoke :class:`RateLimitService.check_allowed` + ``record_send`` at
the right moments.
"""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Any

from playwright.async_api import async_playwright

from bot.modules.fb_session import create_session_context

logger = logging.getLogger(__name__)


# --- exceptions -------------------------------------------------------------


class CommentSendError(Exception):
    """Base class for comment-sender errors."""


class CookieExpiredError(CommentSendError):
    """Raised when the cookie session is invalid / redirected to login."""


class CheckpointRequiredError(CommentSendError):
    """Raised when FB routes the account to a checkpoint/verification flow."""


# --- DTO --------------------------------------------------------------------


@dataclass(frozen=True)
class SendResult:
    """Outcome of a single :func:`send_comment` call.

    ``success=True`` means the comment was typed, submitted, and the
    posted-comment article node was detected in the DOM. ``fb_comment_id``
    is best-effort — FB's new composer often doesn't expose a stable
    integer id, so the caller should treat ``None`` as acceptable.
    """

    success: bool
    comment_text: str
    post_url: str
    fb_comment_id: str | None = None
    error: str | None = None
    checkpoint: bool = False


# --- constants --------------------------------------------------------------

DEFAULT_DELAY_RANGE_MS: tuple[int, int] = (50, 150)
"""Per-character typing delay range (ms) when ``delay_range_ms`` omitted."""

_PAGE_TIMEOUT_MS: int = 45_000
_COMPOSER_WAIT_MS: int = 15_000
_POSTED_COMMENT_WAIT_MS: int = 20_000
_POST_SUBMIT_SETTLE_MS: int = 1500

_TEXTBOX_SELECTOR = (
    'div[contenteditable="true"][role="textbox"]'
    '[aria-label^="Comment as"]'
)
_LEAVE_A_COMMENT_BUTTON = (
    'div[role="button"][aria-label="Leave a comment"]'
)
_POST_COMMENT_BUTTON = 'div[role="button"][aria-label="Post comment"]'

# Patterns that indicate FB rerouted us away from the target post.
_LOGIN_URL_FRAGMENTS = ("/login", "/login.php", "/recover")
_CHECKPOINT_URL_FRAGMENTS = ("/checkpoint", "/confirmemail")


# --- helpers ----------------------------------------------------------------


def _is_login_redirect(url: str) -> bool:
    lower = url.lower()
    return any(frag in lower for frag in _LOGIN_URL_FRAGMENTS)


def _is_checkpoint_redirect(url: str) -> bool:
    lower = url.lower()
    return any(frag in lower for frag in _CHECKPOINT_URL_FRAGMENTS)


async def _sleep_ms(ms: int) -> None:
    await asyncio.sleep(ms / 1000)


async def _type_humanlike(
    page: Any,
    textbox: Any,
    text: str,
    *,
    delay_range_ms: tuple[int, int],
) -> None:
    """Focus textbox and emit characters with randomised per-char delay."""
    try:
        await textbox.focus()
    except Exception:  # pragma: no cover — some mocks skip focus
        pass

    lo, hi = delay_range_ms
    if lo < 0:
        lo = 0
    if hi < lo:
        hi = lo

    # Prefer page.keyboard.type so composer state updates fire naturally.
    # Playwright's delay arg is per-char ms, but we loop for random jitter.
    keyboard = getattr(page, "keyboard", None)
    if keyboard is not None and hasattr(keyboard, "type"):
        for ch in text:
            delay = random.randint(lo, hi) if hi > lo else lo
            await keyboard.type(ch, delay=delay)
        return

    # Fallback: type via the element itself.
    for ch in text:
        delay = random.randint(lo, hi) if hi > lo else lo
        await textbox.type(ch, delay=delay)


async def _find_composer(page: Any) -> Any | None:
    """Locate the comment textbox, expanding composer first if needed."""
    # Fast path — composer already rendered.
    textbox = await page.query_selector(_TEXTBOX_SELECTOR)
    if textbox is not None:
        return textbox

    # Try clicking the "Leave a comment" stub to expand the composer.
    leave_btn = await page.query_selector(_LEAVE_A_COMMENT_BUTTON)
    if leave_btn is not None:
        try:
            await leave_btn.click()
            await page.wait_for_timeout(800)
        except Exception:
            pass

    try:
        return await page.wait_for_selector(
            _TEXTBOX_SELECTOR, timeout=_COMPOSER_WAIT_MS
        )
    except Exception:
        return None


async def _find_post_button(page: Any) -> Any | None:
    btn = await page.query_selector(_POST_COMMENT_BUTTON)
    if btn is not None:
        return btn
    try:
        return await page.wait_for_selector(
            _POST_COMMENT_BUTTON, timeout=_COMPOSER_WAIT_MS
        )
    except Exception:
        return None


async def _wait_posted_comment(
    page: Any, display_name: str, text: str
) -> Any | None:
    safe_name = display_name.replace('"', "")
    selector = (
        f'div[role="article"][aria-label^="Comment by {safe_name}"]'
    )
    try:
        node = await page.wait_for_selector(
            selector, timeout=_POSTED_COMMENT_WAIT_MS
        )
    except Exception:
        return None
    if node is None:
        return None
    try:
        inner = await node.inner_text()
        if text and text[:40] in (inner or ""):
            return node
    except Exception:
        pass
    return node


# --- public API -------------------------------------------------------------


async def send_comment(
    *,
    post_url: str,
    comment_text: str,
    cookies: dict[str, str],
    display_name: str,
    delay_range_ms: tuple[int, int] = DEFAULT_DELAY_RANGE_MS,
    headless: bool = True,
    user_agent: str | None = None,
) -> SendResult:
    """Post ``comment_text`` as a comment under ``post_url``.

    Hard pre-flight errors (empty inputs, missing ``c_user`` cookie,
    login/checkpoint redirect) raise the dedicated exceptions. Soft
    failures (composer missing, post button missing, verification miss)
    return ``SendResult(success=False, error=...)`` so the caller can log
    without blowing the whole job up.
    """
    if not post_url or not post_url.strip():
        raise CommentSendError("post_url kosong bro — kasih URL post dulu.")
    stripped = (comment_text or "").strip()
    if not stripped:
        raise CommentSendError(
            "comment_text kosong — minimal 1 karakter bukan whitespace."
        )
    if not cookies or "c_user" not in cookies:
        raise CookieExpiredError(
            "Cookies kosong atau gak ada c_user — session invalid."
        )

    browser = None
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=headless)
            context = await create_session_context(
                browser, cookies, user_agent=user_agent
            )
            page = await context.new_page()

            await page.goto(
                post_url,
                timeout=_PAGE_TIMEOUT_MS,
                wait_until="domcontentloaded",
            )

            current_url = getattr(page, "url", "") or ""
            if _is_checkpoint_redirect(current_url):
                raise CheckpointRequiredError(
                    f"Redirect ke checkpoint saat buka {post_url} "
                    f"— akun butuh verifikasi manual."
                )
            if _is_login_redirect(current_url):
                raise CookieExpiredError(
                    f"Redirect ke login saat buka {post_url} — cookie expired."
                )

            try:
                await page.wait_for_load_state(
                    "networkidle", timeout=15_000
                )
            except Exception:
                logger.debug("networkidle timeout on %s — continuing", post_url)
            await page.wait_for_timeout(1500)

            textbox = await _find_composer(page)
            if textbox is None:
                return SendResult(
                    success=False,
                    comment_text=stripped,
                    post_url=post_url,
                    error="Comment composer ga ketemu — DOM mungkin berubah.",
                )

            await _type_humanlike(
                page, textbox, stripped, delay_range_ms=delay_range_ms
            )
            await page.wait_for_timeout(random.randint(200, 500))

            post_btn = await _find_post_button(page)
            if post_btn is None:
                return SendResult(
                    success=False,
                    comment_text=stripped,
                    post_url=post_url,
                    error=(
                        "Tombol 'Post comment' ga ketemu — "
                        "composer mungkin belum ready."
                    ),
                )

            await post_btn.click()
            await page.wait_for_timeout(_POST_SUBMIT_SETTLE_MS)

            # Re-check URL post-submit in case FB routed us away.
            current_url = getattr(page, "url", "") or ""
            if _is_checkpoint_redirect(current_url):
                raise CheckpointRequiredError(
                    "Checkpoint muncul setelah klik Post comment — "
                    "akun ditahan FB."
                )

            posted = await _wait_posted_comment(
                page, display_name, stripped
            )
            if posted is None:
                return SendResult(
                    success=False,
                    comment_text=stripped,
                    post_url=post_url,
                    error=(
                        "Komen udah di-submit tapi verifikasi node "
                        "'Comment by ...' ga ketemu."
                    ),
                )

            fb_comment_id: str | None = None
            try:
                raw_id = await posted.get_attribute("id")
                if raw_id:
                    fb_comment_id = str(raw_id)
            except Exception:
                fb_comment_id = None

            return SendResult(
                success=True,
                comment_text=stripped,
                post_url=post_url,
                fb_comment_id=fb_comment_id,
            )
        finally:
            if browser is not None:
                try:
                    await browser.close()
                except Exception:  # pragma: no cover
                    pass
