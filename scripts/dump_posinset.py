"""Dump all aria-labels + engagement text blocks from a live posinset."""
import asyncio
import sqlite3

from playwright.async_api import async_playwright

from bot.modules.fb_session import create_session_context
from server.crypto import decrypt_cookies


async def main() -> None:
    conn = sqlite3.connect("bot/data/app.db")
    enc = conn.execute(
        "SELECT cookies_encrypted FROM fb_accounts WHERE id=1"
    ).fetchone()[0]
    cookies = decrypt_cookies(enc)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await create_session_context(browser, cookies)
        page = await ctx.new_page()
        await page.goto(
            "https://www.facebook.com/home.php",
            timeout=45_000,
            wait_until="domcontentloaded",
        )
        try:
            await page.wait_for_load_state("networkidle", timeout=12_000)
        except Exception:
            pass
        for _ in range(3):
            await page.evaluate("window.scrollBy(0, window.innerHeight*2)")
            await page.wait_for_timeout(2500)

        data = await page.evaluate(
            """() => {
                const posts = Array.from(document.querySelectorAll('div[aria-posinset]')).slice(0, 4);
                return posts.map(p => {
                    const aria = Array.from(p.querySelectorAll('[aria-label]'))
                        .map(e => e.getAttribute('aria-label'))
                        .filter(l => l && l.length < 150);
                    const links = Array.from(p.querySelectorAll('a[href]'))
                        .map(a => a.getAttribute('href'))
                        .filter(h => h);
                    // Find elements that look like engagement counters ("12 reactions", "5 comments", "3 shares").
                    const engagementEls = Array.from(p.querySelectorAll('span, div'))
                        .map(e => (e.innerText || '').trim())
                        .filter(t => /^[\\d,.KMkm]+\\s*(reactions?|likes?|comments?|shares?|people)/i.test(t)
                                    || /^\\d+$/.test(t));
                    return {
                        posinset: p.getAttribute('aria-posinset'),
                        aria_labels: aria,
                        post_links: links.filter(l => /\\/posts\\/|\\/permalink\\/|story_fbid|\\/groups\\/.+\\/posts\\/|\\/photo\\/\\?fbid=|\\/videos\\/\\d+/.test(l)).slice(0, 6),
                        all_links_sample: links.slice(0, 8),
                        engagement_text: engagementEls.slice(0, 15),
                    };
                });
            }"""
        )
        for i, d in enumerate(data):
            print(f"\n=== POSINSET {i} posinset={d['posinset']} ===")
            print("post_links:")
            for L in d["post_links"]:
                print(f"  {L[:110]}")
            print("all_links_sample:")
            for L in d["all_links_sample"]:
                print(f"  {L[:110]}")
            print("aria_labels:")
            for a in d["aria_labels"]:
                print(f"  {a}")
            print("engagement_text:")
            for e in d["engagement_text"]:
                print(f"  {e}")

        await browser.close()


asyncio.run(main())
