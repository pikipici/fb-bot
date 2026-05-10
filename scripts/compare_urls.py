"""Compare home_feed URL variants for posinset hydration."""
import asyncio
import sqlite3

from playwright.async_api import async_playwright

from bot.modules.fb_session import create_session_context
from server.crypto import decrypt_cookies


URLS = [
    "https://www.facebook.com/?sk=h_chr",
    "https://www.facebook.com/home.php",
    "https://m.facebook.com/home.php",
    "https://mbasic.facebook.com/home.php",
]


async def main() -> None:
    conn = sqlite3.connect("bot/data/app.db")
    enc = conn.execute(
        "SELECT cookies_encrypted FROM fb_accounts WHERE id=1"
    ).fetchone()[0]
    cookies = decrypt_cookies(enc)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        for url in URLS:
            ctx = await create_session_context(browser, cookies)
            page = await ctx.new_page()
            await page.goto(url, timeout=45_000, wait_until="domcontentloaded")
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                pass
            # scroll 3x
            for _ in range(3):
                await page.evaluate("window.scrollBy(0, window.innerHeight*2)")
                await page.wait_for_timeout(2000)
            data = await page.evaluate(
                """() => ({
                    role_article: document.querySelectorAll('[role="article"]').length,
                    posinset: document.querySelectorAll('div[aria-posinset]').length,
                    post_links: document.querySelectorAll('a[href*="/posts/"], a[href*="/permalink/"], a[href*="story_fbid"]').length,
                })"""
            )
            print(f"{url}\n  final={page.url}\n  {data}")
            await ctx.close()
        await browser.close()


asyncio.run(main())
