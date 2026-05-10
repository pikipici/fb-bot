"""Scroll each posinset into view, then dump innerText + html_len + sample children."""
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

        # Scroll each posinset into view to trigger hydration.
        await page.evaluate(
            """async () => {
                const posts = document.querySelectorAll('div[aria-posinset]');
                for (const p of posts) {
                    p.scrollIntoView({block: 'center'});
                    await new Promise(r => setTimeout(r, 600));
                }
            }"""
        )
        await page.wait_for_timeout(3000)

        data = await page.evaluate(
            """() => {
                const posts = Array.from(document.querySelectorAll('div[aria-posinset]'));
                return posts.slice(0, 6).map(p => {
                    const links = Array.from(p.querySelectorAll('a[href]'))
                        .map(a => a.getAttribute('href') || '').filter(h => h);
                    const aria = Array.from(p.querySelectorAll('[aria-label]'))
                        .map(e => e.getAttribute('aria-label'))
                        .filter(l => l && l.length < 150);
                    const text = (p.innerText || '').slice(0, 250);
                    const imgs = Array.from(p.querySelectorAll('img[src]'))
                        .map(i => i.getAttribute('src'))
                        .filter(s => s && s.includes('scontent'))
                        .slice(0, 2);
                    return {
                        posinset: p.getAttribute('aria-posinset'),
                        html_len: p.outerHTML.length,
                        text_len: p.innerText ? p.innerText.length : 0,
                        text: text,
                        links_count: links.length,
                        links_sample: links.slice(0, 6),
                        aria_labels: aria.slice(0, 10),
                        imgs: imgs,
                    };
                });
            }"""
        )
        for d in data:
            print(f"\n=== posinset={d['posinset']} html_len={d['html_len']} text_len={d['text_len']} ===")
            print(f"text: {d['text']}")
            print(f"links_count: {d['links_count']}")
            print("links_sample:")
            for L in d["links_sample"]:
                print(f"  {L[:110]}")
            print("aria_labels:")
            for a in d["aria_labels"]:
                print(f"  {a}")
            print(f"imgs: {d['imgs']}")

        await browser.close()


asyncio.run(main())
