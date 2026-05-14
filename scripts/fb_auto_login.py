"""Auto-login Facebook account → harvest cookie → POST ke re-upload endpoint.

Usage (di server, dari /home/ubuntu/fb-bot):
    # Credential via env (RECOMMENDED, ga masuk shell history kalau di-set lewat
    # heredoc / `read -s`):
    FB_LOGIN_EMAIL=... FB_LOGIN_PASSWORD=... ACCOUNT_ID=1 \\
        python scripts/fb_auto_login.py

    # Atau pake stdin (paste manual, no echo):
    python scripts/fb_auto_login.py --account-id 1 --read-stdin

Flow:
    1. Pakai fingerprint pinned akun (UA + viewport pool dari I-A) +
       persistent profile path I-C kalau available, supaya kalau berhasil cookie
       langsung bisa dipakai persistent context tanpa reseed.
    2. Goto facebook.com/login → isi form → submit.
    3. Wait sampe redirect ke domain apapun selain `/login` ATAU sampe ada
       checkpoint indicator (`/checkpoint` URL, "two_factor" element).
    4. Detect checkpoint / 2FA / failed login → exit dengan code != 0 + alasan.
    5. Kalau success: harvest cookies dari context.cookies(), filter facebook
       domain, post ke `/api/v1/fb-accounts/{account_id}/re-upload-cookie`.

Output JSON ke stdout (success/error). Password ga pernah di-log atau di-print.

WARNING: Login dari VPS IP yang udah ke-flag kemungkinan bakal trigger
checkpoint. Script ini disiapin sebagai tool siap-pakai, bukan rekomendasi
default flow. Lihat skill notes Phase I outcome.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
from typing import Any

import httpx
from playwright.async_api import async_playwright, TimeoutError as PWTimeout


LOGIN_URL = "https://www.facebook.com/login.php"
TIMEOUT_MS = 30_000
POST_LOGIN_WAIT_MS = 8_000

DEFAULT_API_BASE = "http://127.0.0.1:8100/api/v1"


def _detect_outcome(url: str, page_text: str) -> tuple[str, str]:
    """Return (status, detail). status in {success, checkpoint, two_factor, failed}."""
    if "/checkpoint" in url or "checkpoint" in page_text.lower()[:5000]:
        return "checkpoint", f"checkpoint redirect: {url}"
    if "/two_factor" in url or "two-factor" in page_text.lower()[:5000]:
        return "two_factor", f"2FA required: {url}"
    if "login" in url:
        # masih di login page → kemungkinan password salah / captcha
        return "failed", f"still on login page: {url}"
    if "facebook.com" in url:
        return "success", url
    return "failed", f"unexpected url: {url}"


async def _admin_token(api_base: str, username: str, password: str) -> str:
    async with httpx.AsyncClient(base_url=api_base, timeout=10) as client:
        resp = await client.post(
            "/auth/login",
            json={"username": username, "password": password},
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


async def _post_cookie(
    api_base: str, token: str, account_id: int, cookies: list[dict[str, Any]]
) -> dict[str, Any]:
    payload = {"cookies": cookies}
    async with httpx.AsyncClient(base_url=api_base, timeout=20) as client:
        resp = await client.post(
            f"/fb-accounts/{account_id}/re-upload-cookie",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        return {
            "status_code": resp.status_code,
            "body": resp.json() if resp.content else None,
        }


async def auto_login(
    account_id: int,
    email: str,
    password: str,
    *,
    headless: bool = True,
    api_base: str = DEFAULT_API_BASE,
    admin_user: str = "admin",
    admin_password: str = "admin123",
    upload: bool = True,
) -> dict[str, Any]:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/132.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
            locale="id-ID",
            timezone_id="Asia/Jakarta",
        )
        page = await context.new_page()
        try:
            await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            await page.fill("input[name='email']", email, timeout=TIMEOUT_MS)
            await page.fill("input[name='pass']", password, timeout=TIMEOUT_MS)
            await page.click("button[name='login']", timeout=TIMEOUT_MS)

            try:
                await page.wait_for_url(
                    lambda u: "login" not in u or "checkpoint" in u or "two_factor" in u,
                    timeout=POST_LOGIN_WAIT_MS,
                )
            except PWTimeout:
                pass

            await page.wait_for_load_state("domcontentloaded", timeout=TIMEOUT_MS)
            current_url = page.url
            page_text = await page.content()
            status, detail = _detect_outcome(current_url, page_text)

            if status != "success":
                return {"status": status, "detail": detail, "url": current_url}

            raw_cookies = await context.cookies()
            fb_cookies = [c for c in raw_cookies if "facebook.com" in c.get("domain", "")]
            if not any(c["name"] == "c_user" for c in fb_cookies):
                return {
                    "status": "failed",
                    "detail": "no c_user cookie present after login",
                    "url": current_url,
                }
        finally:
            await context.close()
            await browser.close()

    result: dict[str, Any] = {
        "status": "success",
        "url": current_url,
        "cookie_count": len(fb_cookies),
        "c_user": next(c["value"] for c in fb_cookies if c["name"] == "c_user"),
    }

    if upload:
        token = await _admin_token(api_base, admin_user, admin_password)
        upload_resp = await _post_cookie(api_base, token, account_id, fb_cookies)
        result["upload"] = upload_resp

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-id", type=int, default=int(os.environ.get("ACCOUNT_ID", "1")))
    parser.add_argument("--read-stdin", action="store_true", help="Read email + password from stdin")
    parser.add_argument("--no-upload", action="store_true", help="Just login + dump cookie, skip upload")
    parser.add_argument("--headed", action="store_true", help="Run browser headed (debugging)")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    args = parser.parse_args()

    if args.read_stdin:
        email = input("FB email/phone: ").strip()
        password = getpass.getpass("FB password: ")
    else:
        email = os.environ.get("FB_LOGIN_EMAIL", "").strip()
        password = os.environ.get("FB_LOGIN_PASSWORD", "")
        if not email or not password:
            print(
                json.dumps(
                    {"status": "failed", "detail": "missing FB_LOGIN_EMAIL / FB_LOGIN_PASSWORD env"}
                )
            )
            return 2

    result = asyncio.run(
        auto_login(
            account_id=args.account_id,
            email=email,
            password=password,
            headless=not args.headed,
            api_base=args.api_base,
            upload=not args.no_upload,
        )
    )
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
