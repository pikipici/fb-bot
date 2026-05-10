"""Debug: scan home_feed dan simpan screenshot + HTML buat inspect."""
import asyncio
import sqlite3
import sys

from playwright.async_api import async_playwright

from bot.modules.fb_session import create_session_context
from server.crypto import decrypt_cookies


async def debug_scan():
    conn = sqlite3.connect("bot/data/app.db")
    enc = conn.execute(
        "SELECT cookies_encrypted FROM fb_accounts WHERE id=1"
    ).fetchone()[0]
    cookies = decrypt_cookies(enc)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await create_session_context(browser, cookies)
        page = await context.new_page()

        url = "https://www.facebook.com/?sk=h_chr"
        print(f"GOTO {url}")
        await page.goto(url, timeout=30_000, wait_until="domcontentloaded")
        print(f"FINAL_URL: {page.url}")

        # Wait for network to settle before probing the DOM.
        await page.wait_for_timeout(5000)

        html_len = len(await page.content())
        print(f"HTML_LEN: {html_len}")

        # How many <div role="article"> are present right after load?
        articles = await page.evaluate(
            "document.querySelectorAll('[role=\"article\"]').length"
        )
        print(f"ARTICLES_ON_LOAD: {articles}")

        # Scroll once and re-count.
        await page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
        await page.wait_for_timeout(3000)
        articles_scrolled = await page.evaluate(
            "document.querySelectorAll('[role=\"article\"]').length"
        )
        print(f"ARTICLES_AFTER_SCROLL: {articles_scrolled}")

        # Look for any permalink-shaped anchors.
        post_links = await page.evaluate("""
            Array.from(document.querySelectorAll(
                'a[href*="/posts/"], a[href*="story_fbid"], a[href*="/permalink/"]'
            )).slice(0, 5).map(a => a.href)
        """)
        print(f"POST_LINKS[:5]: {post_links}")

        # Sample text content of first article if any.
        sample = await page.evaluate("""
            const art = document.querySelector('[role="article"]');
            if (!art) return null;
            return {
                text_len: art.innerText.length,
                snippet: art.innerText.slice(0, 120),
                has_msg: !!art.querySelector('[data-ad-preview="message"]'),
                has_comet: !!art.querySelector('[data-ad-comet-above-more-text]'),
            };
        """)
        print(f"SAMPLE: {sample}")

        await browser.close()


asyncio.run(debug_scan())
