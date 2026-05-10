"""Check home feed composer presence (proves auth) + try alternative photo URLs."""
import asyncio
import sqlite3
import sys
from playwright.async_api import async_playwright
from bot.modules.fb_session import create_session_context
from server.crypto import decrypt_cookies


async def probe(page, label, url):
    print(f"\n=== {label} ===")
    print(f"url: {url}")
    try:
        await page.goto(url, timeout=45_000, wait_until="domcontentloaded")
    except Exception as e:
        print(f"goto failed: {e}")
        return
    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:
        pass
    await page.wait_for_timeout(3000)

    r = await page.evaluate("""
        () => ({
            finalUrl: location.href,
            title: document.title,
            editables: document.querySelectorAll('div[contenteditable="true"]').length,
            textboxes: document.querySelectorAll('[role="textbox"]').length,
            dialogs: document.querySelectorAll('[role="dialog"]').length,
            loginBtn: !!document.querySelector('a[href*="login"]') || !!Array.from(document.querySelectorAll('div[role="button"]')).find(el => /login|masuk/i.test(el.getAttribute('aria-label') || '')),
            bodyText200: (document.body.innerText || '').slice(0, 200),
        })
    """)
    print(f"finalUrl: {r['finalUrl']}")
    print(f"title:    {r['title']}")
    print(f"editables={r['editables']} textboxes={r['textboxes']} dialogs={r['dialogs']} loginBtn={r['loginBtn']}")
    print(f"body[:200]: {r['bodyText200']!r}")


async def main():
    fbid = "827774990387713"
    setid = "a.487596357738913"

    db = sqlite3.connect("bot/data/app.db")
    row = db.execute("SELECT cookies_encrypted FROM fb_accounts WHERE status='ACTIVE' LIMIT 1").fetchone()
    cookies = decrypt_cookies(row[0])

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            ctx = await create_session_context(browser, cookies)
            page = await ctx.new_page()

            await probe(page, "HOME", "https://www.facebook.com/")
            await probe(page, "PHOTO clean", f"https://www.facebook.com/photo/?fbid={fbid}&set={setid}")
            await probe(page, "PHOTO .php", f"https://www.facebook.com/photo.php?fbid={fbid}&set={setid}")
            await probe(page, "PHOTO permalink", f"https://www.facebook.com/photo/?fbid={fbid}")
            await probe(page, "m.facebook photo", f"https://m.facebook.com/photo.php?fbid={fbid}")

        finally:
            await browser.close()


asyncio.run(main())
