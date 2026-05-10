"""Probe: mirror the text-based fallback from comment_sender.py fix.

Goes to post URL, scrolls bottom, tries text-based click on Komentari stub,
then checks if contenteditable composer hydrates.
"""
import asyncio
import sqlite3
import sys
from playwright.async_api import async_playwright
from bot.modules.fb_session import create_session_context
from server.crypto import decrypt_cookies


PROBE_JS = """
() => {
  const editables = Array.from(
    document.querySelectorAll('div[contenteditable="true"]')
  ).map((el) => ({
    role: el.getAttribute('role'),
    ariaLabel: el.getAttribute('aria-label'),
    placeholder: el.getAttribute('aria-placeholder'),
    visible: el.offsetParent !== null,
  }));
  return {
    url: location.href,
    editablesCount: editables.length,
    editables: editables.slice(0, 10),
  };
};
"""

LEAVE_TEXTS = ("Komentari", "Leave a comment", "Tulis komentar", "Beri komentar", "Komen")


async def main():
    post_url = sys.argv[1]
    db = sqlite3.connect("bot/data/app.db")
    row = db.execute(
        "SELECT cookies_encrypted FROM fb_accounts WHERE status='ACTIVE' LIMIT 1"
    ).fetchone()
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
            await page.wait_for_timeout(1500)

            # scroll to bottom 5x (mirror comment_sender fix)
            for _ in range(5):
                await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(600)

            print("\n--- ROUND 1: after scroll-to-bottom ---")
            r = await page.evaluate(PROBE_JS)
            print(f"editables: {r['editablesCount']}")
            for e in r["editables"]:
                print(f"  {e}")

            if r["editablesCount"] > 0:
                print(">> composer already hydrated, no click needed")
                return

            # text-based click (mirror the fix)
            clicked = False
            for label in LEAVE_TEXTS:
                try:
                    loc = page.get_by_text(label, exact=True).first
                    count = await loc.count()
                    if count > 0:
                        print(f"\n>> clicking text={label!r} (count={count})")
                        await loc.click(timeout=3000)
                        clicked = True
                        break
                except Exception as e:
                    print(f"   text={label!r} failed: {type(e).__name__}: {e}")

            if clicked:
                await page.wait_for_timeout(2500)
                print("\n--- ROUND 2: after text-based stub click ---")
                r = await page.evaluate(PROBE_JS)
                print(f"editables: {r['editablesCount']}")
                for e in r["editables"]:
                    print(f"  {e}")

                # Try the actual textbox selector from comment_sender
                textbox_sel = (
                    'div[contenteditable="true"][role="textbox"][aria-label*="komentar" i], '
                    'div[contenteditable="true"][role="textbox"][aria-label*="comment" i]'
                )
                try:
                    await page.wait_for_selector(textbox_sel, timeout=10_000, state="visible")
                    print(f"\n>> textbox FOUND via sender selector ✓")
                except Exception as e:
                    print(f"\n>> textbox NOT found via sender selector: {type(e).__name__}")
            else:
                print("\n>> no stub matched any text label")

        finally:
            await browser.close()


asyncio.run(main())
