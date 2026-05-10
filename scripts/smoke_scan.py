"""Smoke test: scan real home_feed and dump posts."""
import asyncio
import sqlite3

from bot.modules.source_collector import scan_source
from server.crypto import decrypt_cookies


async def main() -> None:
    conn = sqlite3.connect("bot/data/app.db")
    enc = conn.execute(
        "SELECT cookies_encrypted FROM fb_accounts WHERE id=1"
    ).fetchone()[0]
    cookies = decrypt_cookies(enc)

    src = {"id": 1, "type": "home_feed"}
    result = await scan_source(src, cookies)

    print(f"success: {result.success}  error: {result.error!r}")
    print(f"posts: {len(result.posts)}")
    for p in result.posts[:10]:
        pid = p["fb_post_id"][:30]
        author = p["author_name"][:25]
        likes = p["likes"]
        comments = p["comments"]
        shares = p["shares"]
        text_snippet = p["text"][:50]
        print(
            f"  - {pid:<32} author={author:<27} "
            f"likes={likes:>5} comments={comments:>3} shares={shares:>3} "
            f"text={text_snippet!r}"
        )


if __name__ == "__main__":
    asyncio.run(main())
