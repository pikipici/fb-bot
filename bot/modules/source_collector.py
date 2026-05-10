"""Source-aware collector — Playwright scrape with cookie session.

Separate from :mod:`bot.modules.collector` (the legacy target-based
pipeline). This module knows about :class:`server.models.Source` rows
and drives a single scrape pass per source:

    source -> build_source_url() -> launch chromium + cookie context
           -> goto URL -> verify not redirected to /login
           -> scroll N times, extract posts per iteration
           -> dedup by fb_post_id -> return SourceCollectorResult

Called by the celery beat task ``scan_all_sources`` which then pipes the
posts through :mod:`keyword_filter` and :mod:`trending_scorer` before
upserting into ``trending_posts``.

Extraction strategy:
- Extract ``fb_post_id`` from the permalink path (``/posts/<id>``,
  ``story_fbid=<id>``, or ``?fbid=<id>``). This becomes the unique
  dedup key across scrolls and across scans.
- Counts are parsed from ``aria-label`` text because FB's DOM doesn't
  expose numeric values as attributes on anything reliable.
- Per-scroll dedup keeps us from emitting the same post multiple times
  as new articles overlap with previously-seen ones.

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
from typing import Any, Final

from playwright.async_api import async_playwright

from bot.modules.fb_session import DEFAULT_USER_AGENT, create_session_context

logger = logging.getLogger(__name__)

SOURCE_SCROLL_COUNT: Final = 3
_SCROLL_DELAY_MIN: Final = 3.0
_SCROLL_DELAY_MAX: Final = 8.0
_PAGE_TIMEOUT_MS: Final = 30_000
_POST_WAIT_MS: Final = 1_500


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
        return "https://www.facebook.com/?sk=h_chr"
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

# JS that runs in the page context and extracts post metadata from every
# ``<div role="article">`` currently rendered. Counts come from aria-labels
# because FB doesn't expose numeric engagement as structured data.
_EXTRACT_POSTS_JS = r"""
() => {
    const results = [];
    const articles = document.querySelectorAll('[role="article"]');

    const extractPostId = (href) => {
        if (!href) return null;
        const m1 = href.match(/\/posts\/(?:pfbid[^/?#]+|\d+)/);
        if (m1) return m1[0].split('/').pop();
        const m2 = href.match(/[?&]story_fbid=(\d+)/);
        if (m2) return m2[1];
        const m3 = href.match(/[?&]fbid=(\d+)/);
        if (m3) return m3[1];
        const m4 = href.match(/\/permalink\/(\d+)/);
        if (m4) return m4[1];
        return null;
    };

    const parseCount = (label) => {
        if (!label) return 0;
        const m = label.toLowerCase().match(/([\d.,]+)\s*([kKmM])?/);
        if (!m) return 0;
        let num = parseFloat(m[1].replace(/,/g, ''));
        if (isNaN(num)) return 0;
        const suffix = (m[2] || '').toLowerCase();
        if (suffix === 'k') num *= 1_000;
        else if (suffix === 'm') num *= 1_000_000;
        return Math.round(num);
    };

    for (const article of articles) {
        const textEl = article.querySelector(
            '[data-ad-preview="message"], [data-ad-comet-above-more-text]'
        );
        const authorEl = article.querySelector('h3 a, h4 a, strong a');
        const linkEl = article.querySelector(
            'a[href*="/posts/"], a[href*="story_fbid"], a[href*="/permalink/"]'
        );
        const imgEl = article.querySelector('img[src*="scontent"]');

        const postId = extractPostId(linkEl ? linkEl.href : null);
        if (!postId) continue;

        let likes = 0, comments = 0, shares = 0;
        const engagementEls = article.querySelectorAll(
            '[aria-label*="like"], [aria-label*="reaction"],'
            + '[aria-label*="comment"], [aria-label*="share"]'
        );
        for (const el of engagementEls) {
            const label = (el.getAttribute('aria-label') || '').toLowerCase();
            const n = parseCount(label);
            if (n <= 0) continue;
            if (label.includes('share') && shares === 0) shares = n;
            else if (label.includes('comment') && comments === 0) comments = n;
            else if ((label.includes('like') || label.includes('reaction'))
                     && likes === 0) likes = n;
        }

        results.push({
            fb_post_id: postId,
            author_name: authorEl ? authorEl.innerText.trim() : '',
            text: textEl ? textEl.innerText.trim() : '',
            likes: likes,
            comments: comments,
            shares: shares,
            post_url: linkEl ? linkEl.href : '',
            thumbnail_url: imgEl ? imgEl.src : null,
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
    max_posts: int = 40,
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
                browser, cookies, user_agent=user_agent
            )
            page = await context.new_page()

            await page.goto(url, timeout=_PAGE_TIMEOUT_MS, wait_until="domcontentloaded")

            if _is_login_redirect(getattr(page, "url", "") or ""):
                raise CookieExpiredError(
                    f"Redirect ke login saat akses {url} — cookie expired."
                )

            await page.wait_for_timeout(_POST_WAIT_MS)

            for i in range(SOURCE_SCROLL_COUNT):
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

                # Scroll two viewport-heights down.
                try:
                    await page.evaluate(
                        "window.scrollBy(0, window.innerHeight * 2)"
                    )
                except Exception:  # pragma: no cover — defensive
                    logger.debug("scroll eval failed", exc_info=True)
                await page.wait_for_timeout(_POST_WAIT_MS)

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
