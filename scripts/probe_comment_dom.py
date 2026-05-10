"""Probe FB comment DOM: find comment input, submit button, post-after detection.

Run on server:
    set -a && . .env && set +a
    PYTHONPATH=. python scripts/probe_comment_dom.py <post_url>

Prints candidate selectors + attributes so we can pick stable ones for
the comment_sender module. Does NOT type or submit anything.
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys

from playwright.async_api import async_playwright

from bot.modules.fb_session import create_session_context
from server.crypto import decrypt_cookies


PROBE_JS = """
() => {
  const out = {};

  // Candidate comment input: contenteditable textboxes.
  const editables = Array.from(
    document.querySelectorAll('div[contenteditable="true"]')
  );
  out.editables = editables.slice(0, 10).map((el) => ({
    role: el.getAttribute('role'),
    ariaLabel: el.getAttribute('aria-label'),
    dataLexicalEditor: el.getAttribute('data-lexical-editor'),
    ariaPlaceholder: el.getAttribute('aria-placeholder'),
    className: (el.className || '').slice(0, 80),
    parentAriaLabel: el.parentElement?.getAttribute('aria-label') || null,
    visible: el.offsetParent !== null,
    rect: (() => {
      const r = el.getBoundingClientRect();
      return {x: r.x | 0, y: r.y | 0, w: r.width | 0, h: r.height | 0};
    })(),
  }));

  // Submit candidate: look for "Comment" / "Komentar" / paper-plane-ish
  // labelled buttons/divs within form-like containers.
  const labels = ['Comment', 'Komentar', 'Kirim', 'Post', 'Send'];
  const submits = [];
  for (const sel of ['[role="button"]', 'button', 'div[aria-label]']) {
    for (const el of document.querySelectorAll(sel)) {
      const al = el.getAttribute('aria-label') || '';
      if (labels.some((l) => al.toLowerCase().includes(l.toLowerCase()))) {
        submits.push({
          tag: el.tagName,
          ariaLabel: al,
          role: el.getAttribute('role'),
          visible: el.offsetParent !== null,
        });
        if (submits.length >= 15) break;
      }
    }
    if (submits.length >= 15) break;
  }
  out.submits = submits;

  // Is there a "Write a comment" placeholder we need to click first?
  const placeholders = Array.from(
    document.querySelectorAll('[aria-label]')
  )
    .filter((el) => {
      const al = el.getAttribute('aria-label') || '';
      return /comment|komentar|tulis/i.test(al);
    })
    .slice(0, 10)
    .map((el) => ({
      tag: el.tagName,
      role: el.getAttribute('role'),
      ariaLabel: el.getAttribute('aria-label'),
      visible: el.offsetParent !== null,
    }));
  out.placeholders = placeholders;

  out.url = location.href;
  out.title = document.title;
  return out;
}
"""


async def main(post_url: str) -> None:
    conn = sqlite3.connect("bot/data/app.db")
    enc = conn.execute(
        "SELECT cookies_encrypted FROM fb_accounts WHERE id=1"
    ).fetchone()[0]
    cookies = decrypt_cookies(enc)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            ctx = await create_session_context(browser, cookies)
            page = await ctx.new_page()
            print(f">> goto {post_url}")
            await page.goto(
                post_url, timeout=45_000, wait_until="domcontentloaded"
            )
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:
                pass
            await page.wait_for_timeout(2000)

            # Scroll to bottom to reveal comment composer.
            await page.evaluate(
                "() => window.scrollTo(0, document.body.scrollHeight)"
            )
            await page.wait_for_timeout(1500)

            result = await page.evaluate(PROBE_JS)

            print(f"url after goto: {result['url']}")
            print(f"title: {result['title']}")
            print("\n=== contenteditable candidates ===")
            for e in result["editables"]:
                print(f"  {e}")
            print("\n=== submit/post-ish buttons ===")
            for s in result["submits"]:
                print(f"  {s}")
            print("\n=== 'write a comment' placeholders ===")
            for p in result["placeholders"]:
                print(f"  {p}")
        finally:
            await browser.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python scripts/probe_comment_dom.py <post_url>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
