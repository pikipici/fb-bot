"""Probe inside photo viewer modal (role=dialog) for editables and comment panel."""
import asyncio
import sqlite3
import sys
from playwright.async_api import async_playwright
from bot.modules.fb_session import create_session_context
from server.crypto import decrypt_cookies


PROBE_JS = """
() => {
  const dialogs = Array.from(document.querySelectorAll('div[role="dialog"]'));
  const report = [];
  dialogs.forEach((dlg, i) => {
    const editables = Array.from(dlg.querySelectorAll('div[contenteditable="true"]')).map(el => ({
      role: el.getAttribute('role'),
      ariaLabel: el.getAttribute('aria-label'),
      placeholder: el.getAttribute('aria-placeholder'),
      visible: el.offsetParent !== null,
    }));
    const buttons = [];
    for (const el of dlg.querySelectorAll('div[role="button"], button')) {
      const al = el.getAttribute('aria-label') || '';
      const txt = (el.innerText || '').slice(0, 80);
      if (/comment|komentar|leave|tulis|kirim|beri|tinggalkan/i.test(al + ' ' + txt)) {
        buttons.push({
          tag: el.tagName,
          role: el.getAttribute('role'),
          ariaLabel: al,
          text: txt,
          visible: el.offsetParent !== null,
        });
        if (buttons.length >= 10) break;
      }
    }
    report.push({
      index: i,
      ariaLabel: dlg.getAttribute('aria-label'),
      editablesCount: editables.length,
      editables: editables.slice(0, 5),
      commentButtons: buttons,
    });
  });
  return { dialogCount: dialogs.length, dialogs: report };
};
"""


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
            await page.wait_for_timeout(3000)

            print("\n--- ROUND 1: initial dialogs ---")
            r = await page.evaluate(PROBE_JS)
            print(f"dialogCount: {r['dialogCount']}")
            for d in r["dialogs"]:
                print(f"  [{d['index']}] aria={d['ariaLabel']!r} editables={d['editablesCount']} commentBtns={len(d['commentButtons'])}")
                for e in d["editables"]:
                    print(f"      edit: {e}")
                for b in d["commentButtons"]:
                    print(f"      btn:  {b}")

            # scroll inside the first dialog if any
            if r["dialogCount"] > 0:
                print("\n>> trying to scroll inside the modal")
                await page.evaluate("""
                    () => {
                        const dlg = document.querySelector('div[role="dialog"]');
                        if (!dlg) return;
                        // scroll all scrollable descendants
                        const all = dlg.querySelectorAll('*');
                        for (const el of all) {
                            if (el.scrollHeight > el.clientHeight) {
                                el.scrollTop = el.scrollHeight;
                            }
                        }
                    }
                """)
                await page.wait_for_timeout(2000)

                print("\n--- ROUND 2: after scroll inside modal ---")
                r = await page.evaluate(PROBE_JS)
                for d in r["dialogs"]:
                    print(f"  [{d['index']}] editables={d['editablesCount']} commentBtns={len(d['commentButtons'])}")
                    for e in d["editables"]:
                        print(f"      edit: {e}")
                    for b in d["commentButtons"][:5]:
                        print(f"      btn:  {b}")

            # Also dump general composition area: any textbox anywhere
            print("\n--- general textbox scan (document-wide) ---")
            all_tb = await page.evaluate("""
                () => Array.from(document.querySelectorAll('[role="textbox"]')).map(el => ({
                    role: el.getAttribute('role'),
                    ariaLabel: el.getAttribute('aria-label'),
                    tag: el.tagName,
                    contentEditable: el.getAttribute('contenteditable'),
                    visible: el.offsetParent !== null,
                }))
            """)
            print(f"textboxes: {len(all_tb)}")
            for t in all_tb[:10]:
                print(f"  {t}")

        finally:
            await browser.close()


asyncio.run(main())
