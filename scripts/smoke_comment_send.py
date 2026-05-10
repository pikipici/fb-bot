"""Live smoke: send one real comment via CommentSender.

Usage:
    set -a && . .env && set +a
    PYTHONPATH=. python scripts/smoke_comment_send.py <post_id>

<post_id> = trending_posts.id from SQLite. Script loads cookies from
fb_accounts (id=1), fetches post_url, types a short hard-coded safe
comment, and prints result.

⚠️  This ACTUALLY POSTS A COMMENT to Facebook. Pick the target post
carefully. The script does NOT call RateLimitService.record_send() — that
happens in the F5 router wiring. But it WILL be visible under the FB
account's activity.
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys

from bot.modules.comment_sender import (
    CheckpointRequiredError,
    CookieExpiredError,
    send_comment,
)
from server.crypto import decrypt_cookies


SMOKE_COMMENT_TEXT = "mantap bro"
"""Short, neutral, generic comment. Edit locally if you want a different one."""


def _load_target(post_id: int) -> tuple[str, str]:
    conn = sqlite3.connect("bot/data/app.db")
    try:
        row = conn.execute(
            "SELECT post_url FROM trending_posts WHERE id=?",
            (post_id,),
        ).fetchone()
        if row is None:
            raise SystemExit(f"trending_posts id={post_id} not found")
        acc = conn.execute(
            "SELECT display_name, cookies_encrypted FROM fb_accounts "
            "WHERE status='ACTIVE' ORDER BY id LIMIT 1"
        ).fetchone()
        if acc is None:
            raise SystemExit("no active fb_account")
        display_name, enc = acc
        if not display_name:
            raise SystemExit(
                "fb_account.display_name kosong — harus terisi biar "
                "bisa cari 'Comment by <name>' node buat verifikasi"
            )
        return row[0], display_name, decrypt_cookies(enc)
    finally:
        conn.close()


async def _main(post_id: int) -> None:
    post_url, display_name, cookies = _load_target(post_id)
    print(f">> target post_id={post_id}")
    print(f">> post_url={post_url[:90]}...")
    print(f">> display_name={display_name}")
    print(f">> comment_text={SMOKE_COMMENT_TEXT!r}")
    print(">> sending...")

    try:
        result = await send_comment(
            post_url=post_url,
            comment_text=SMOKE_COMMENT_TEXT,
            cookies=cookies,
            display_name=display_name,
        )
    except CookieExpiredError as exc:
        print(f"!! cookie expired: {exc}")
        return
    except CheckpointRequiredError as exc:
        print(f"!! checkpoint required: {exc}")
        return

    print(f"<< success={result.success}")
    print(f"<< comment_text={result.comment_text!r}")
    print(f"<< fb_comment_id={result.fb_comment_id}")
    if result.error:
        print(f"<< error={result.error}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python scripts/smoke_comment_send.py <post_id>")
        sys.exit(1)
    asyncio.run(_main(int(sys.argv[1])))
