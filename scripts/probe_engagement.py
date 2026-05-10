"""Dig for comment/share count markers in a hydrated posinset."""
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

        await page.evaluate(
            """async () => {
                const ps = document.querySelectorAll('div[aria-posinset]');
                for (const p of ps) { p.scrollIntoView({block: 'center'}); await new Promise(r => setTimeout(r, 500)); }
            }"""
        )
        await page.wait_for_timeout(3000)

        data = await page.evaluate(
            """() => {
                const posts = Array.from(document.querySelectorAll('div[aria-posinset]'))
                    .filter(p => p.innerText && p.innerText.length > 50);
                return posts.slice(0, 4).map(p => {
                    const full = p.innerText || '';
                    // Harvest any text that looks like an engagement number.
                    const matches = [];
                    const re = /([\\d,.KM]+)\\s*(reactions?|comments?|shares?|likes?)/gi;
                    let m;
                    while ((m = re.exec(full)) !== null) {
                        matches.push(`${m[1]} ${m[2]}`);
                    }
                    // Also find all aria-labels
                    const allAria = Array.from(p.querySelectorAll('[aria-label]'))
                        .map(e => e.getAttribute('aria-label'))
                        .filter(l => l && l.length < 100);
                    // Check for the 'Leave a comment' button's count sibling
                    const commentBtn = p.querySelector('[aria-label="Leave a comment"]');
                    const shareBtn = p.querySelector('[aria-label*="Send this"], [aria-label*="share"]');
                    return {
                        posinset: p.getAttribute('aria-posinset'),
                        text_len: full.length,
                        matches: matches,
                        comment_btn_parent_text: commentBtn ? (commentBtn.parentElement?.innerText || '').slice(0, 100) : null,
                        share_btn_parent_text: shareBtn ? (shareBtn.parentElement?.innerText || '').slice(0, 100) : null,
                        aria_labels_sample: allAria.slice(0, 30),
                    };
                });
            }"""
        )
        for d in data:
            print(f"\n=== posinset={d['posinset']} len={d['text_len']} ===")
            print(f"numeric matches: {d['matches']}")
            print(f"comment_btn_parent_text: {d['comment_btn_parent_text']}")
            print(f"share_btn_parent_text: {d['share_btn_parent_text']}")
            print("aria_labels:")
            for a in d["aria_labels_sample"]:
                print(f"  {a}")

        await browser.close()


asyncio.run(main())
