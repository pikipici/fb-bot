"""Collector — fetches posts from Facebook targets via scraping or API."""

import asyncio
import logging
import random
from typing import Any

import httpx

from bot.modules.circuit_breaker import CircuitBreaker
from bot.modules.parser import Parser
from bot.modules.rate_guard import RateGuard

logger = logging.getLogger(__name__)

# Default scraping config
DEFAULT_SCROLL_COUNT = 5
DEFAULT_SCROLL_DELAY_MIN = 1.5
DEFAULT_SCROLL_DELAY_MAX = 3.5
DEFAULT_PAGE_TIMEOUT = 30000  # ms
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Selectors for FB public page/group posts (may need updates as FB changes)
POST_CONTAINER_SELECTOR = '[role="article"]'
POST_TEXT_SELECTOR = '[data-ad-preview="message"]'
POST_AUTHOR_SELECTOR = 'h3 a, h4 a, strong a'
POST_LINK_SELECTOR = 'a[href*="/posts/"], a[href*="/permalink/"], a[href*="story_fbid"]'
ENGAGEMENT_SELECTOR = '[aria-label*="like"], [aria-label*="comment"], [aria-label*="share"]'

# Signals that indicate blocking/captcha
BLOCK_SIGNALS = [
    "checkpoint",
    "captcha",
    "you must log in",
    "content isn't available",
    "this content isn't available",
]


class CollectorResult:
    """Result of a collection run for a single target."""

    def __init__(
        self,
        target_id: str,
        posts: list[dict[str, Any]],
        success: bool = True,
        error: str = "",
        blocked: bool = False,
    ):
        self.target_id = target_id
        self.posts = posts
        self.success = success
        self.error = error
        self.blocked = blocked

    def __repr__(self) -> str:
        return (
            f"CollectorResult(target={self.target_id}, "
            f"posts={len(self.posts)}, success={self.success})"
        )


class Collector:
    """Fetch posts from Facebook targets."""

    def __init__(
        self,
        rate_guard: RateGuard | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        parser: Parser | None = None,
        config: dict[str, Any] | None = None,
    ):
        self.rate_guard = rate_guard or RateGuard({})
        self.circuit_breaker = circuit_breaker or CircuitBreaker()
        self.parser = parser or Parser()
        self.config = config or {}

    async def collect_target(self, target: dict[str, Any]) -> CollectorResult:
        """Collect posts from a single target.

        Checks circuit breaker and rate guard before proceeding. Routes
        to API or scrape mode based on target config. On any error we
        ``rate_guard.release(target_id)`` so a failed fetch does not
        consume a slot — callers can retry without being unfairly
        throttled.
        """
        target_id = target["id"]

        if not self.circuit_breaker.is_available(target_id):
            logger.warning("Target %s suspended by circuit breaker", target_id)
            return CollectorResult(target_id, [], success=False, error="suspended")

        if not self.rate_guard.check_and_reserve(target_id):
            logger.info("Target %s rate limited", target_id)
            return CollectorResult(target_id, [], success=False, error="rate_limited")

        mode = target.get("mode", "scrape_public")

        try:
            if mode == "api_first":
                posts = await self._collect_via_api(target)
            else:
                posts = await self._collect_via_scrape(target)

            self.circuit_breaker.record_success(target_id)
            logger.info("Collected %d posts from %s (%s)", len(posts), target_id, mode)
            return CollectorResult(target_id, posts)

        except BlockDetectedError as e:
            self.rate_guard.release(target_id)
            self.circuit_breaker.record_failure(target_id)
            logger.error("Block detected for %s: %s", target_id, e)
            return CollectorResult(
                target_id, [], success=False, error=str(e), blocked=True
            )
        except (CredentialError, ConfigurationError) as e:
            # Shared / config problem — do NOT trip the circuit breaker
            # (every target would suspend in turn for the same cause).
            self.rate_guard.release(target_id)
            logger.error("Collection aborted for %s: %s", target_id, e)
            return CollectorResult(target_id, [], success=False, error=str(e))
        except Exception as e:
            self.rate_guard.release(target_id)
            self.circuit_breaker.record_failure(target_id)
            logger.error("Collection failed for %s: %s", target_id, e)
            return CollectorResult(target_id, [], success=False, error=str(e))

    async def _collect_via_scrape(self, target: dict[str, Any]) -> list[dict[str, Any]]:
        """Collect posts via Playwright headless browser scraping."""
        from playwright.async_api import async_playwright

        url = target.get("url", "")
        max_posts = target.get("max_posts_per_run", 50)
        scroll_count = self.config.get("scroll_count", DEFAULT_SCROLL_COUNT)
        user_agent = self.config.get("user_agent", DEFAULT_USER_AGENT)

        raw_posts: list[dict[str, Any]] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=user_agent,
                viewport={"width": 1280, "height": 720},
                locale="id-ID",
            )
            page = await context.new_page()

            try:
                await page.goto(url, timeout=DEFAULT_PAGE_TIMEOUT, wait_until="domcontentloaded")

                # Check for block signals
                page_content = await page.content()
                self._check_block_signals(page_content)

                # Scroll and collect
                for i in range(scroll_count):
                    # Extract posts currently visible
                    new_posts = await self._extract_posts_from_page(page)
                    raw_posts.extend(new_posts)

                    if len(raw_posts) >= max_posts:
                        break

                    # Random delay between scrolls
                    delay = random.uniform(DEFAULT_SCROLL_DELAY_MIN, DEFAULT_SCROLL_DELAY_MAX)
                    await asyncio.sleep(delay)

                    # Scroll down
                    await page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
                    await page.wait_for_timeout(1000)  # Wait for content load

                    # Re-check for blocks after scroll
                    page_content = await page.content()
                    self._check_block_signals(page_content)

            finally:
                await browser.close()

        # Deduplicate by fb_post_id or URL
        raw_posts = self._deduplicate_raw(raw_posts)

        # Limit to max_posts
        raw_posts = raw_posts[:max_posts]

        # Parse and normalize
        return self.parser.parse_scraped_posts(raw_posts, target)

    async def _extract_posts_from_page(self, page: Any) -> list[dict[str, Any]]:
        """Extract raw post data from current page state."""
        posts = await page.evaluate("""() => {
            const articles = document.querySelectorAll('[role="article"]');
            const results = [];

            for (const article of articles) {
                const textEl = article.querySelector('[data-ad-preview="message"]')
                    || article.querySelector('[data-ad-comet-above-more-text]')
                    || article.querySelector('.x1iorvi4');
                const authorEl = article.querySelector('h3 a, h4 a, strong a');
                const linkEl = article.querySelector(
                    'a[href*="/posts/"], a[href*="/permalink/"], a[href*="story_fbid"]'
                );

                // Try to get engagement numbers
                const engagementEls = article.querySelectorAll(
                    '[aria-label*="like"], [aria-label*="reaction"], '
                    + '[aria-label*="comment"], [aria-label*="share"]'
                );
                let likes = 0, comments = 0, shares = 0;
                for (const el of engagementEls) {
                    const label = (el.getAttribute('aria-label') || '').toLowerCase();
                    const numMatch = label.match(/(\\d[\\d,.]*)/);
                    const num = numMatch ? parseInt(numMatch[1].replace(/[,.]/g, '')) : 0;
                    if (label.includes('like') || label.includes('reaction')) likes = num;
                    else if (label.includes('comment')) comments = num;
                    else if (label.includes('share')) shares = num;
                }

                results.push({
                    text: textEl ? textEl.innerText.trim() : '',
                    author_name: authorEl ? authorEl.innerText.trim() : '',
                    url: linkEl ? linkEl.href : '',
                    likes: likes,
                    comments: comments,
                    shares: shares,
                });
            }
            return results;
        }""")
        return posts

    async def _collect_via_api(self, target: dict[str, Any]) -> list[dict[str, Any]]:
        """Collect posts via Facebook Graph API.

        The access token is sent via the ``Authorization: Bearer``
        header so it does not land in access logs or proxy logs. If
        the token is missing we raise ``ConfigurationError`` — the old
        silent fallback to scraping violated the target's explicit
        ``api_first`` mode choice.

        Token-expiry errors (Graph error code 190) raise
        :class:`CredentialError` instead of :class:`BlockDetectedError`
        so the circuit breaker does not suspend every target one by
        one for what is really a single global credential problem.
        """
        api_token = self.config.get("graph_api_token", "")
        if not api_token:
            raise ConfigurationError(
                f"Target {target.get('id')} is api_first but graph_api_token is empty"
            )

        target_fb_id = target.get("fb_id", target.get("id", ""))
        max_posts = target.get("max_posts_per_run", 50)

        url = (
            f"https://graph.facebook.com/v18.0/{target_fb_id}/feed"
            f"?fields=id,message,created_time,from,reactions.summary(true),"
            f"comments.summary(true),shares"
            f"&limit={min(max_posts, 100)}"
        )
        headers = {"Authorization": f"Bearer {api_token}"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)

            if response.status_code == 400:
                error_data = response.json().get("error", {})
                if error_data.get("code") == 190:
                    raise CredentialError("Graph API token expired or invalid")
                raise RuntimeError(
                    f"Graph API error: {error_data.get('message', 'unknown')}"
                )

            if response.status_code == 429:
                raise BlockDetectedError("Graph API rate limit hit")

            response.raise_for_status()
            api_data = response.json()

        return self.parser.parse_api_posts(api_data, target)

    def _check_block_signals(self, page_content: str):
        """Check page content for block/captcha signals."""
        content_lower = page_content.lower()
        for signal in BLOCK_SIGNALS:
            if signal in content_lower:
                raise BlockDetectedError(f"Block signal detected: {signal}")

    def _deduplicate_raw(self, posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove duplicate raw posts based on URL or text content."""
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []

        for post in posts:
            key = post.get("url") or post.get("text", "")[:100]
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(post)

        return unique


class BlockDetectedError(Exception):
    """Raised when Facebook block/captcha is detected for a single target."""

    pass


class CredentialError(Exception):
    """Raised when a shared credential is invalid (e.g. Graph token expired).

    This is intentionally separate from :class:`BlockDetectedError` so
    callers can route it to a global alert path instead of tripping the
    per-target circuit breaker for every target in turn.
    """

    pass


class ConfigurationError(Exception):
    """Raised when a target's configuration is internally inconsistent.

    For example, ``mode=api_first`` without any Graph API token set.
    """

    pass
