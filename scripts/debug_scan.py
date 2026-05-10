"""Debug: scan home_feed dan simpan screenshot + HTML buat inspect.

Run:
    cd /home/ubuntu/fb-bot
    set -a && . .env && set +a
    PYTHONPATH=. venv/bin/python scripts/debug_scan.py [www|m|mbasic]
"""
import asyncio
import sqlite3
import sys

from playwright.async_api import async_playwright

from bot.modules.fb_session import create_session_context
from server.crypto import decrypt_cookies


VARIANTS = {
    "www": "https://www.facebook.com/?sk=h_chr",
    "m": "https://m.facebook.com/home.php",
    "mbasic": "https://mbasic.facebook.com/home.php",
}


async def debug_scan(variant: str = "www") -> None:
    url = VARIANTS[variant]

    conn = sqlite3.connect("bot/data/app.db")
    enc = conn.execute(
        "SELECT cookies_encrypted FROM fb_accounts WHERE id=1"
    ).fetchone()[0]
    cookies = decrypt_cookies(enc)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await create_session_context(browser, cookies)
        page = await context.new_page()

        print(f"VARIANT: {variant}")
        print(f"GOTO {url}")
        await page.goto(url, timeout=45_000, wait_until="domcontentloaded")
        print(f"FINAL_URL_AFTER_DOMLOAD: {page.url}")

        # Let lazy-loaded content settle.
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception as exc:
            print(f"networkidle_timeout: {exc}")
        print(f"FINAL_URL_AFTER_IDLE: {page.url}")

        html = await page.content()
        print(f"HTML_LEN: {len(html)}")

        # Save artifacts for manual inspection.
        out_dir = "/tmp"
        with open(f"{out_dir}/fb_debug_{variant}.html", "w", encoding="utf-8") as f:
            f.write(html)
        await page.screenshot(path=f"{out_dir}/fb_debug_{variant}.png", full_page=False)
        print(f"SAVED: {out_dir}/fb_debug_{variant}.html + .png")

        # Detect login / checkpoint walls.
        login_signals = await page.evaluate(
            """() => ({
                has_login_form: !!document.querySelector('input[name="email"], input[name="pass"]'),
                has_checkpoint: /checkpoint/i.test(location.pathname),
                has_login_button: !!document.querySelector('a[href*="/login"], button[name="login"]'),
                title: document.title,
            })"""
        )
        print(f"LOGIN_SIGNALS: {login_signals}")

        # Probe a broad set of selectors to learn FB's current DOM.
        selector_counts = await page.evaluate(
            """() => {
                const sel = [
                    '[role="article"]',
                    '[role="feed"] > div',
                    '[data-pagelet^="FeedUnit_"]',
                    '[data-pagelet="Feed"]',
                    '[data-pagelet="HomeStream"]',
                    '[data-ad-preview="message"]',
                    '[data-ad-comet-above-more-text]',
                    'div[aria-posinset]',
                    'article',
                    'div[data-visualcompletion="ignore-dynamic"]',
                ];
                const out = {};
                for (const s of sel) {
                    out[s] = document.querySelectorAll(s).length;
                }
                return out;
            }"""
        )
        print("SELECTOR_COUNTS:")
        for k, v in selector_counts.items():
            print(f"  {v:>4}  {k}")

        # Scroll a few times to trigger lazy hydration, re-count key selectors.
        for i in range(3):
            await page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
            await page.wait_for_timeout(2500)
        post_scroll = await page.evaluate(
            """() => ({
                role_article: document.querySelectorAll('[role="article"]').length,
                feed_children: document.querySelectorAll('[role="feed"] > div').length,
                aria_posinset: document.querySelectorAll('div[aria-posinset]').length,
                feed_unit_pagelet: document.querySelectorAll('[data-pagelet^="FeedUnit_"]').length,
            })"""
        )
        print(f"AFTER_SCROLL: {post_scroll}")

        # Collect candidate permalink anchors.
        post_links = await page.evaluate(
            """() => Array.from(document.querySelectorAll(
                'a[href*="/posts/"], a[href*="story_fbid"], a[href*="/permalink/"], a[href*="/videos/"], a[href*="/photo/"]'
            )).slice(0, 8).map(a => a.href)"""
        )
        print(f"POST_LINKS[:8]: {post_links}")

        # Sample first article if present.
        sample = await page.evaluate(
            """() => {
                const art = document.querySelector('[role="article"]')
                    || document.querySelector('[role="feed"] > div')
                    || document.querySelector('div[aria-posinset]');
                if (!art) return null;
                return {
                    matched: art.getAttribute('role') || art.tagName,
                    text_len: art.innerText ? art.innerText.length : 0,
                    snippet: art.innerText ? art.innerText.slice(0, 200) : '',
                };
            }"""
        )
        print(f"SAMPLE: {sample}")

        await browser.close()


if __name__ == "__main__":
    variant = sys.argv[1] if len(sys.argv) > 1 else "www"
    if variant not in VARIANTS:
        print(f"unknown variant {variant!r}, pick from {list(VARIANTS)}")
        sys.exit(2)
    asyncio.run(debug_scan(variant))
