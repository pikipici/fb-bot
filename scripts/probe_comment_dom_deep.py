"""Deeper DOM probe — wait longer, click to expand, then re-probe."""
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

  // Find any element that says anything like "write a comment" / "tulis komentar" / "leave a comment"
  const matches = [];
  const allBtns = document.querySelectorAll('div[role="button"], button, [role="textbox"]');
  for (const el of allBtns) {
    const al = el.getAttribute('aria-label') || '';
    const txt = (el.innerText || '').slice(0, 100);
    if (/comment|komentar|leave|tulis|kirim|beri|tinggalkan/i.test(al) || /comment|komentar|leave|tulis|kirim|beri|tinggalkan/i.test(txt)) {
      matches.push({
        tag: el.tagName,
        role: el.getAttribute('role'),
        ariaLabel: al,
        text: txt,
        visible: el.offsetParent !== null,
      });
      if (matches.length >= 20) break;
    }
  }

  return {
    url: location.href,
    editablesCount: editables.length,
    editables: editables.slice(0, 10),
    commentMatches: matches,
  };
};
"""


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
            await page.wait_for_timeout(3000)

            print("\n--- ROUND 1: fresh load ---")
            r = await page.evaluate(PROBE_JS)
            print(f"url: {r['url']}")
            print(f"editables: {r['editablesCount']}")
            print(f"matches: {len(r['commentMatches'])}")
            for m in r["commentMatches"][:10]:
                print(f"  {m}")

            # Scroll hard
            for _ in range(3):
                await page.evaluate(
                    "() => window.scrollTo(0, document.body.scrollHeight)"
                )
                await page.wait_for_timeout(1000)

            print("\n--- ROUND 2: after 3x scroll ---")
            r = await page.evaluate(PROBE_JS)
            print(f"editables: {r['editablesCount']}")
            for m in r["commentMatches"][:10]:
                print(f"  {m}")

            # Try clicking on any visible "leave/tulis comment" stub
            leave_selectors = [
                'div[role="button"][aria-label*="comment" i]',
                'div[role="button"][aria-label*="komentar" i]',
                'div[role="button"][aria-label*="tulis" i]',
                'div[role="button"][aria-label*="leave" i]',
            ]
            clicked = False
            for sel in leave_selectors:
                btn = await page.query_selector(sel)
                if btn:
                    try:
                        label = await btn.get_attribute("aria-label")
                        print(f"\n>> clicking: {sel} (label={label!r})")
                        await btn.click()
                        clicked = True
                        break
                    except Exception as e:
                        print(f"   click failed: {e}")

            if clicked:
                await page.wait_for_timeout(2500)
                print("\n--- ROUND 3: after clicking stub ---")
                r = await page.evaluate(PROBE_JS)
                print(f"editables: {r['editablesCount']}")
                for e in r["editables"]:
                    print(f"  {e}")

            # Try scrolling a specific comment region into view
            print("\n--- ROUND 4: scroll first comment article into view ---")
            try:
                await page.evaluate("""
                    () => {
                        const art = document.querySelector('div[role="article"][aria-label*="omentar"]');
                        if (art) { art.scrollIntoView({ block: 'center' }); return art.getAttribute('aria-label'); }
                        return null;
                    }
                """)
                await page.wait_for_timeout(2000)
                r = await page.evaluate(PROBE_JS)
                print(f"editables: {r['editablesCount']}")
                for e in r["editables"]:
                    print(f"  {e}")
            except Exception as e:
                print(f"scroll-to-article failed: {e}")

        finally:
            await browser.close()

asyncio.run(main())
