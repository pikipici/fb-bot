"""Dump everything inside the photo modal to understand its structure."""
import asyncio
import sqlite3
import sys
from playwright.async_api import async_playwright
from bot.modules.fb_session import create_session_context
from server.crypto import decrypt_cookies


async def main():
    post_url = sys.argv[1]
    db = sqlite3.connect("bot/data/app.db")
    row = db.execute("SELECT cookies_encrypted FROM fb_accounts WHERE status='ACTIVE' LIMIT 1").fetchone()
    cookies = decrypt_cookies(row[0])

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            ctx = await create_session_context(browser, cookies)
            page = await ctx.new_page()
            print(f">> goto {post_url}")
            await page.goto(post_url, timeout=45_000, wait_until="domcontentloaded")
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:
                pass
            await page.wait_for_timeout(4000)

            # dump modal size + all aria-labeled elements inside
            result = await page.evaluate("""
                () => {
                    const dlg = document.querySelector('div[role="dialog"]');
                    if (!dlg) return { error: 'no dialog' };
                    const rect = dlg.getBoundingClientRect();
                    const allAria = Array.from(dlg.querySelectorAll('[aria-label]')).map(el => ({
                        tag: el.tagName,
                        role: el.getAttribute('role'),
                        ariaLabel: el.getAttribute('aria-label'),
                        visible: el.offsetParent !== null,
                    }));
                    const allButtons = Array.from(dlg.querySelectorAll('div[role="button"], button')).slice(0, 30).map(el => ({
                        tag: el.tagName,
                        role: el.getAttribute('role'),
                        ariaLabel: el.getAttribute('aria-label') || '',
                        text: (el.innerText || '').slice(0, 60),
                        visible: el.offsetParent !== null,
                    }));
                    return {
                        dialogSize: { w: rect.width, h: rect.height },
                        childCount: dlg.querySelectorAll('*').length,
                        innerTextSample: (dlg.innerText || '').slice(0, 500),
                        allAriaCount: allAria.length,
                        allAria: allAria.slice(0, 30),
                        buttonCount: allButtons.length,
                        buttons: allButtons,
                    };
                }
            """)
            print("\n=== MODAL DUMP ===")
            if "error" in result:
                print(result)
                return
            print(f"size: {result['dialogSize']}")
            print(f"childCount: {result['childCount']}")
            print(f"innerText sample: {result['innerTextSample']!r}")
            print(f"\naria-labeled elements ({result['allAriaCount']}):")
            for a in result["allAria"]:
                print(f"  {a}")
            print(f"\nbuttons ({result['buttonCount']}):")
            for b in result["buttons"]:
                print(f"  {b}")

            # save full HTML
            html = await page.content()
            # just dialog content
            dlg_html = await page.evaluate("""
                () => {
                    const dlg = document.querySelector('div[role="dialog"]');
                    return dlg ? dlg.outerHTML.length : 0;
                }
            """)
            print(f"\ndialog outerHTML length: {dlg_html}")

            # check full page editables + textboxes (not just modal)
            print("\n=== FULL PAGE SCAN ===")
            full = await page.evaluate("""
                () => {
                    return {
                        bodyEditables: document.querySelectorAll('div[contenteditable="true"]').length,
                        bodyTextboxes: document.querySelectorAll('[role="textbox"]').length,
                        bodyDialogs: document.querySelectorAll('[role="dialog"]').length,
                        bodyArticles: document.querySelectorAll('[role="article"]').length,
                    };
                }
            """)
            print(full)

        finally:
            await browser.close()


asyncio.run(main())
