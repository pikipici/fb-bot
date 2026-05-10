"""Live probe: scroll then dump first article outerHTML."""
import asyncio
import sqlite3
import sys

from playwright.async_api import async_playwright

from bot.modules.fb_session import create_session_context
from server.crypto import decrypt_cookies


async def main(variant: str = "m") -> None:
    urls = {
        "www": "https://www.facebook.com/?sk=h_chr",
        "m": "https://m.facebook.com/home.php",
    }
    url = urls[variant]

    conn = sqlite3.connect("bot/data/app.db")
    enc = conn.execute(
        "SELECT cookies_encrypted FROM fb_accounts WHERE id=1"
    ).fetchone()[0]
    cookies = decrypt_cookies(enc)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await create_session_context(browser, cookies)
        page = await context.new_page()

        await page.goto(url, timeout=45_000, wait_until="domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass

        # Scroll aggressively to trigger hydration.
        for _ in range(4):
            await page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
            await page.wait_for_timeout(2500)

        data = await page.evaluate(
            """() => {
                const arts = Array.from(document.querySelectorAll('[role="article"]'));
                return arts.slice(0, 3).map(a => ({
                    aria_label: a.getAttribute('aria-label'),
                    links: Array.from(a.querySelectorAll('a[href]'))
                        .map(x => x.getAttribute('href'))
                        .filter(h => h)
                        .slice(0, 10),
                    abbrs: Array.from(a.querySelectorAll('abbr')).map(x => x.innerText).slice(0, 3),
                    text: (a.innerText || '').slice(0, 300),
                    html_len: a.outerHTML.length,
                    aria_labels_inside: Array.from(a.querySelectorAll('[aria-label]'))
                        .map(x => x.getAttribute('aria-label'))
                        .filter(l => l && l.length < 80)
                        .slice(0, 20),
                }));
            }"""
        )

        print(f"VARIANT: {variant}  URL: {url}")
        print(f"FINAL_URL: {page.url}")
        print(f"ARTICLES_FOUND: {len(data)}")
        for i, art in enumerate(data):
            print(f"\n--- ARTICLE {i} ---")
            print(f"aria-label: {art['aria_label']}")
            print(f"html_len: {art['html_len']}")
            print(f"abbrs: {art['abbrs']}")
            print(f"links ({len(art['links'])}):")
            for L in art["links"]:
                print(f"  {L[:100]}")
            print(f"text: {art['text']}")
            print(f"aria_labels_inside: {art['aria_labels_inside']}")

        # Save first article HTML.
        if data:
            html0 = await page.evaluate(
                """() => {
                    const art = document.querySelector('[role="article"]');
                    return art ? art.outerHTML : null;
                }"""
            )
            if html0:
                with open(f"/tmp/fb_article0_{variant}.html", "w", encoding="utf-8") as f:
                    f.write(html0)
                print(f"\nSAVED: /tmp/fb_article0_{variant}.html ({len(html0)} bytes)")

        await browser.close()


if __name__ == "__main__":
    variant = sys.argv[1] if len(sys.argv) > 1 else "m"
    asyncio.run(main(variant))
