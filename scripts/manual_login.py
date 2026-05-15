"""Manual login launcher — opens FB in a persistent Chromium context.

Usage (di server, via systemd display:3 + noVNC tunnel):

    cd /home/ubuntu/fb-bot
    source venv/bin/activate
    DISPLAY=:3 python scripts/manual_login.py

Lalu buka noVNC di browser laptop (lewat ssh tunnel localhost:6083/vnc.html),
login manual ke Facebook, terus kembali ke terminal dan tekan Enter untuk
menutup browser dengan rapi (penting: jangan close window paksa, biar profile
flush bersih ke disk).

Setelah Enter, script akan:
  - flush + close context
  - update fb_accounts.status = 'ACTIVE'
  - reset failure_count, clear cookies_expired_at
  - print profile size for sanity check

Account ID default = 1 (env: FB_ACCOUNT_ID).
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

# Make sure bot package is importable
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.async_api import async_playwright

from bot.modules.browser_profile import get_profile_path
from bot.modules.fb_session import create_persistent_session_context


FB_HOME = "https://www.facebook.com/"


async def amain() -> int:
    account_id = int(os.environ.get("FB_ACCOUNT_ID", "1"))
    profile_dir = get_profile_path(account_id)

    print(f"[manual_login] account_id={account_id}")
    print(f"[manual_login] profile_dir={profile_dir}")
    print(f"[manual_login] DISPLAY={os.environ.get('DISPLAY', '<unset>')}")
    print()

    async with async_playwright() as pw:
        # Pakai launcher yang sama dengan production scan path biar
        # fingerprint identik (UA, viewport, locale, tz, stealth init).
        context = await create_persistent_session_context(
            pw,
            account_id=account_id,
            cookies=None,        # don't bootstrap from DB; profile is authoritative
            headless=False,      # MUST be False; user needs to see + interact
        )

        try:
            page = await context.new_page()
            await page.goto(FB_HOME, wait_until="domcontentloaded", timeout=30_000)

            print("[manual_login] FB opened. Lakukan login lewat noVNC sekarang.")
            print("[manual_login] Setelah selesai login + verify feed loaded,")
            print("[manual_login] balik ke terminal dan tekan Enter untuk close bersih.")
            print()

            # Block on stdin — user kasih signal ketika login done
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, input, ">>> Tekan Enter setelah login selesai: ")

            # Sanity check: are we logged in? FB redirects to /home/ or sets c_user cookie
            cookies = await context.cookies()
            c_user = next((c for c in cookies if c["name"] == "c_user"), None)
            xs = next((c for c in cookies if c["name"] == "xs"), None)
            if not c_user or not xs:
                print("[manual_login] WARN: c_user / xs cookie ga ketemu. Login mungkin belum sukses.")
                print(f"[manual_login] cookies present: {[c['name'] for c in cookies]}")
                print("[manual_login] Tetap close dan abort — fix dulu sebelum re-enable account.")
                return 2

            print(f"[manual_login] OK: c_user={c_user['value']} xs.len={len(xs['value'])}")

        finally:
            print("[manual_login] Closing context (flush profile to disk)...")
            await context.close()

    # Profile size check
    if profile_dir.exists():
        total = sum(p.stat().st_size for p in profile_dir.rglob("*") if p.is_file())
        print(f"[manual_login] profile size: {total / 1_048_576:.1f} MB")

    print(f"[manual_login] Done at {datetime.utcnow().isoformat()}Z")
    print("[manual_login] Next: update DB account status, then trigger manual scan to verify.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
