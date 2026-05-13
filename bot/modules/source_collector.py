"""Source-aware collector — Playwright scrape with cookie session.

Separate from :mod:`bot.modules.collector` (the legacy target-based
pipeline). This module knows about :class:`server.models.Source` rows
and drives a single scrape pass per source:

    source -> build_source_url() -> launch chromium + cookie context
           -> goto URL -> verify not redirected to /login
           -> scroll each posinset into view (force hydration)
           -> extract posts -> dedup -> return SourceCollectorResult

Called by the celery beat task ``scan_all_sources`` which then pipes
posts through :mod:`keyword_filter` and :mod:`trending_scorer` before
upserting into ``trending_posts``.

Extraction strategy (2026 FB DOM):
- Posts are ``div[aria-posinset]`` — NOT ``[role="article"]`` (those
  are comment-section placeholders that render as "Loading..." in the
  virtualized feed).
- The feed is virtualized: a posinset only hydrates when scrolled into
  view, so we iterate ``scrollIntoView`` on every posinset before
  extract.
- ``fb_post_id`` is derived from any permalink-shaped anchor inside the
  posinset (``/posts/<id>``, ``/reel/<id>``, ``?fbid=<id>``,
  ``story_fbid=<id>``, ``/permalink/<id>``, ``/stories/<uid>/<token>``).
- Author name comes from the ``aria-label="Hide post by <Name>"``
  because that's the only reliably stable author attribution across
  home-feed, group, and page sources.
- Reactions come from aria-labels like ``"Like: 349 people"`` /
  ``"Haha: 129 people"`` summed across all reaction types.
- Comments / shares come from the innerText of the parent element of
  the ``Leave a comment`` / ``Send this to friends`` buttons.

Cookie expiry:
- After ``page.goto``, if the final URL contains ``/login`` or
  ``checkpoint`` we raise :class:`CookieExpiredError`. The caller is
  expected to mark the :class:`FBAccount` status and pause scans.
"""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Final

from playwright.async_api import async_playwright

from bot.modules.fb_auth_probe import is_login_wall, login_wall_reason
from bot.modules.fb_session import (
    DEFAULT_USER_AGENT,
    capture_cookies_from_context,
    create_session_context,
)

logger = logging.getLogger(__name__)

SOURCE_SCROLL_COUNT: Final = 3
_SCROLL_DELAY_MIN: Final = 3.0
_SCROLL_DELAY_MAX: Final = 8.0
_PAGE_TIMEOUT_MS: Final = 45_000
_INITIAL_SETTLE_MS: Final = 3_000
_HYDRATION_WAIT_MS: Final = 2_000


class CookieExpiredError(Exception):
    """Raised when the cookie session is invalid / redirected to login."""


@dataclass
class SourceCollectorResult:
    source_id: int
    posts: list[dict[str, Any]] = field(default_factory=list)
    success: bool = True
    error: str = ""


def build_source_url(source: dict[str, Any]) -> str:
    """Resolve the scrape URL for a source row."""
    source_type = source.get("type")
    if source_type == "home_feed":
        # /?sk=h_chr looks right but never hydrates in headless chromium —
        # posinsets stay at "Loading..." placeholders. /home.php renders
        # the same chronological-ish feed and hydrates reliably.
        return "https://www.facebook.com/home.php"
    if source_type == "group":
        entity = source.get("fb_entity_id")
        if entity:
            return f"https://www.facebook.com/groups/{entity}"
        url = source.get("url")
        if url:
            return url
        raise ValueError(f"group source missing entity id and url")
    if source_type == "page":
        entity = source.get("fb_entity_id")
        if entity:
            return f"https://www.facebook.com/{entity}/posts"
        url = source.get("url")
        if url:
            return url if url.endswith("/posts") else url.rstrip("/") + "/posts"
        raise ValueError(f"page source missing entity id and url")
    raise ValueError(f"unknown source type: {source_type!r}")


# --- page-side extraction --------------------------------------------------

# Hydration pass: scroll every posinset into view so the virtual list
# renders their contents before we extract.
_HYDRATE_POSTS_JS = r"""
async () => {
    const posts = document.querySelectorAll('div[aria-posinset]');
    let hydrated = 0;
    for (const p of posts) {
        p.scrollIntoView({block: 'center'});
        await new Promise(r => setTimeout(r, 450));
        if ((p.innerText || '').length > 50) hydrated += 1;
    }
    // Bottom-scroll to trigger FB's infinite loader for the next batch.
    window.scrollTo(0, document.body.scrollHeight);
    return {posts_found: posts.length, posts_hydrated: hydrated};
}
"""

# Extract metadata from every hydrated ``div[aria-posinset]`` currently
# rendered. Returns an array of post dicts.
_EXTRACT_POSTS_JS = r"""
() => {
    const REACTIONS = ['like', 'love', 'haha', 'wow', 'sad', 'angry', 'care'];

    const parseCount = (txt) => {
        if (!txt) return 0;
        const m = String(txt).trim().match(/([\d.,]+)\s*([KkMm])?/);
        if (!m) return 0;
        let n = parseFloat(m[1].replace(/,/g, ''));
        if (isNaN(n)) return 0;
        const s = (m[2] || '').toLowerCase();
        if (s === 'k') n *= 1_000;
        else if (s === 'm') n *= 1_000_000;
        return Math.round(n);
    };

    const extractPostId = (href) => {
        if (!href) return null;
        let m;
        if ((m = href.match(/\/posts\/(pfbid[^/?#]+|\d+)/))) return m[1];
        if ((m = href.match(/[?&]story_fbid=(\d+)/))) return 'sf' + m[1];
        if ((m = href.match(/[?&]fbid=(\d+)/))) return 'fb' + m[1];
        if ((m = href.match(/\/permalink\/(\d+)/))) return 'pl' + m[1];
        if ((m = href.match(/\/reel\/(\d+)/))) return 'rl' + m[1];
        if ((m = href.match(/\/videos\/(\d+)/))) return 'vd' + m[1];
        if ((m = href.match(/\/stories\/\d+\/([^/?#]+)/))) return m[1];
        return null;
    };

    const results = [];
    const posts = Array.from(document.querySelectorAll('div[aria-posinset]'));

    for (const p of posts) {
        const html = p.outerHTML || '';
        if (html.length < 1000) continue;  // not hydrated yet

        const topAria = p.getAttribute('aria-label') || '';
        if (/^reels/i.test(topAria)) continue;  // carousel, not a post

        // Post permalink.
        let postId = null;
        let postUrl = '';
        const anchors = Array.from(p.querySelectorAll('a[href]'));
        for (const a of anchors) {
            const id = extractPostId(a.getAttribute('href') || '');
            if (id) {
                postId = id;
                postUrl = a.href;
                break;
            }
        }
        if (!postId) continue;

        // Author from "Hide post by X" aria-label (stable across contexts).
        let author = '';
        const hideEl = p.querySelector('[aria-label^="Hide post by "]');
        if (hideEl) {
            author = (hideEl.getAttribute('aria-label') || '')
                .replace(/^Hide post by\s+/, '').trim();
        }
        if (!author) {
            // Fallback: "Name, view story" aria-label
            const viewEl = Array.from(p.querySelectorAll('[aria-label]'))
                .find(e => /,\s*view story$/.test(e.getAttribute('aria-label') || ''));
            if (viewEl) {
                author = (viewEl.getAttribute('aria-label') || '')
                    .replace(/,\s*view story$/, '').trim();
            }
        }
        if (!author) continue;

        // Text body.
        let text = '';
        const textEl = p.querySelector(
            '[data-ad-preview="message"], [data-ad-comet-above-more-text], [data-ad-rendering-role="story_message"]'
        );
        if (textEl) text = (textEl.innerText || '').trim();

        // Reactions: sum "Like: X people", "Haha: Y people", etc.
        let reactions = 0;
        for (const el of p.querySelectorAll('[aria-label]')) {
            const lbl = (el.getAttribute('aria-label') || '').trim();
            const m = lbl.match(/^([A-Za-z]+):\s*([\d.,KkMm]+)(?:\s*(?:people|person))?$/);
            if (m && REACTIONS.includes(m[1].toLowerCase())) {
                reactions += parseCount(m[2]);
            }
        }

        // Comments: parent text of "Leave a comment" button.
        let comments = 0;
        const cBtn = p.querySelector('[aria-label="Leave a comment"]');
        if (cBtn && cBtn.parentElement) {
            comments = parseCount((cBtn.parentElement.innerText || '').trim());
        }

        // Shares: parent text of "Send this to friends" button.
        let shares = 0;
        const sBtn = p.querySelector(
            '[aria-label^="Send this to friends"], [aria-label*="share"]'
        );
        if (sBtn && sBtn.parentElement) {
            shares = parseCount((sBtn.parentElement.innerText || '').trim());
        }

        // Thumbnail.
        let thumb = null;
        const img = p.querySelector('img[src*="scontent"]');
        if (img) thumb = img.getAttribute('src');

        results.push({
            fb_post_id: postId,
            author_name: author,
            text: text,
            likes: reactions,     // total reactions; scorer sums with comments+shares
            comments: comments,
            shares: shares,
            post_url: postUrl,
            thumbnail_url: thumb,
        });
    }
    return results;
}
"""


def _post_key(post: dict[str, Any]) -> str:
    return post.get("fb_post_id") or post.get("post_url") or ""


def _is_login_redirect(current_url: str) -> bool:
    lowered = (current_url or "").lower()
    return "/login" in lowered or "checkpoint" in lowered


def _augment_post(post: dict[str, Any]) -> dict[str, Any]:
    likes = int(post.get("likes") or 0)
    comments = int(post.get("comments") or 0)
    shares = int(post.get("shares") or 0)
    return {
        **post,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "reactions_total": likes + comments + shares,
    }


async def scan_source(
    source: dict[str, Any],
    cookies: dict[str, str],
    *,
    user_agent: str = DEFAULT_USER_AGENT,
    viewport: dict[str, int] | None = None,
    max_posts: int = 40,
    on_cookies_refresh: Callable[
        [dict[str, str]], Awaitable[None]
    ] | None = None,
) -> SourceCollectorResult:
    """Scrape one source and return its posts.

    Args:
        source: plain dict snapshot of a ``Source`` row. Must include
            ``id`` and ``type``.
        cookies: decrypted cookie dict (must contain ``c_user``).
        user_agent: UA to pin for this scan.
        max_posts: upper bound on returned posts after dedup.
    """
    source_id = int(source.get("id", 0))

    if not cookies or "c_user" not in cookies:
        raise CookieExpiredError(
            "Cookies kosong atau gak ada c_user — session invalid."
        )

    url = build_source_url(source)

    raw_posts: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    browser = None
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await create_session_context(
                browser, cookies, user_agent=user_agent, viewport=viewport,
            )
            page = await context.new_page()

            await page.goto(
                url, timeout=_PAGE_TIMEOUT_MS, wait_until="domcontentloaded"
            )

            if _is_login_redirect(getattr(page, "url", "") or ""):
                raise CookieExpiredError(
                    f"Redirect ke login saat akses {url} — cookie expired."
                )

            # Let React Suspense boundary render the initial feed skeleton.
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:  # pragma: no cover — transient timeout ok
                logger.debug("networkidle timeout for %s — continuing", url)
            await page.wait_for_timeout(_INITIAL_SETTLE_MS)

            # DOM-based login-wall check. Covers the common case where
            # FB keeps the original URL but renders an account chooser
            # or login form when cookies are stale — the URL-fragment
            # check above only catches explicit redirects.
            if await is_login_wall(page):
                reason = await login_wall_reason(page) or "unknown"
                raise CookieExpiredError(
                    f"Login wall terdeteksi di {url} "
                    f"(reason={reason}) — cookie expired."
                )

            for i in range(SOURCE_SCROLL_COUNT):
                # Hydrate every posinset currently in DOM.
                try:
                    hydration_stats = await page.evaluate(_HYDRATE_POSTS_JS)
                    logger.debug(
                        "scan_source(%s) hydration iter=%s stats=%s",
                        source_id, i, hydration_stats,
                    )
                except Exception:  # pragma: no cover — defensive
                    logger.debug("hydrate eval failed", exc_info=True)
                await page.wait_for_timeout(_HYDRATION_WAIT_MS)

                # Extract hydrated posts.
                extracted = await page.evaluate(_EXTRACT_POSTS_JS)
                for post in extracted or []:
                    key = _post_key(post)
                    if not key or key in seen_ids:
                        continue
                    seen_ids.add(key)
                    raw_posts.append(_augment_post(post))
                    if len(raw_posts) >= max_posts:
                        break
                if len(raw_posts) >= max_posts:
                    break

                delay = random.uniform(_SCROLL_DELAY_MIN, _SCROLL_DELAY_MAX)
                await asyncio.sleep(delay)

            # Phase I-B-3 — harvest any rotated cookies before context
            # closes. Best-effort: swallow callback / capture exceptions
            # so they don't mask the scan result.
            if on_cookies_refresh is not None:
                try:
                    fresh = await capture_cookies_from_context(context)
                    if fresh:
                        await on_cookies_refresh(fresh)
                except Exception:  # noqa: BLE001 — best-effort harvest
                    logger.debug(
                        "cookie refresh callback failed", exc_info=True
                    )

    except CookieExpiredError:
        raise
    except Exception as exc:  # noqa: BLE001 — surface any scrape error
        logger.warning("scan_source(%s) failed: %s", source_id, exc)
        return SourceCollectorResult(
            source_id=source_id,
            posts=raw_posts,
            success=False,
            error=str(exc),
        )
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:  # pragma: no cover — playwright teardown
                logger.debug("browser close failed", exc_info=True)

    return SourceCollectorResult(source_id=source_id, posts=raw_posts)
