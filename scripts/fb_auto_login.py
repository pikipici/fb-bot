"""Auto-login Facebook account → harvest cookie → POST ke re-upload endpoint.

Two-stage flow biar bisa handle 2FA tanpa keep playwright session hidup
selama nunggu user kasih OTP:

Stage 1 (initiate):
    FB_LOGIN_EMAIL=... FB_LOGIN_PASSWORD=... ACCOUNT_ID=1 \\
        python scripts/fb_auto_login.py initiate

  Outcome:
    - "success": cookie langsung valid → auto-upload → done.
    - "two_factor_pending": FB minta OTP. Storage state browser disimpan ke
      disk (`/tmp/fb-login-state-<account>.json`) supaya stage 2 bisa lanjut.
    - "checkpoint" / "failed": stop, no upload.

Stage 2 (continue dengan OTP):
    FB_LOGIN_OTP=123456 ACCOUNT_ID=1 \\
        python scripts/fb_auto_login.py continue

  Outcome:
    - "success": OTP accepted, cookie harvested + uploaded.
    - "failed": OTP salah / expired. Re-run stage 1.

WARNING: Login dari VPS IP yang udah ke-flag bisa trigger checkpoint dan
2FA. Script tetap reject bypass — kalau FB minta OTP, lu HARUS kasih OTP
asli dari email/SMS/authenticator. Password & OTP tidak pernah di-print.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from playwright.async_api import (
    async_playwright,
    BrowserContext,
    Page,
    TimeoutError as PWTimeout,
)


LOGIN_URL = "https://www.facebook.com/login.php"
TIMEOUT_MS = 30_000
POST_SUBMIT_WAIT_MS = 8_000

DEFAULT_API_BASE = "http://127.0.0.1:8100/api/v1"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/132.0.0.0 Safari/537.36"
)
VIEWPORT = {"width": 1366, "height": 768}
LOCALE = "id-ID"
TIMEZONE = "Asia/Jakarta"


def _state_path(account_id: int) -> Path:
    return Path(f"/tmp/fb-login-state-{account_id}.json")


def _classify_url(url: str) -> str:
    """Return one of: success, two_factor, checkpoint, login, unknown."""
    if "/checkpoint" in url:
        return "checkpoint"
    if "/two_step_verification" in url or "/two_factor" in url:
        return "two_factor"
    if "/login" in url:
        return "login"
    if "facebook.com" in url:
        return "success"
    return "unknown"


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
    async with httpx.AsyncClient(base_url=api_base, timeout=20) as client:
        resp = await client.post(
            f"/fb-accounts/{account_id}/re-upload-cookie",
            json={"cookies": cookies},
            headers={"Authorization": f"Bearer {token}"},
        )
        return {
            "status_code": resp.status_code,
            "body": resp.json() if resp.content else None,
        }


async def _upload_cookies(
    context: BrowserContext,
    account_id: int,
    api_base: str,
    admin_user: str,
    admin_password: str,
) -> dict[str, Any]:
    raw_cookies = await context.cookies()
    fb_cookies = [c for c in raw_cookies if "facebook.com" in c.get("domain", "")]
    if not any(c["name"] == "c_user" for c in fb_cookies):
        return {
            "status": "failed",
            "detail": "no c_user cookie present after auth",
        }
    token = await _admin_token(api_base, admin_user, admin_password)
    upload = await _post_cookie(api_base, token, account_id, fb_cookies)
    return {
        "status": "success",
        "cookie_count": len(fb_cookies),
        "c_user": next(c["value"] for c in fb_cookies if c["name"] == "c_user"),
        "upload": upload,
    }


async def _click_login(page: Page) -> None:
    for selector in (
        "button[name='login']",
        "button[data-testid='royal_login_button']",
        "form button[type='submit']",
        "button[type='submit']",
    ):
        btn = await page.query_selector(selector)
        if btn:
            await btn.click(timeout=5_000)
            return
    await page.press("input[name='pass']", "Enter")


async def _click_submit_2fa(page: Page) -> None:
    for selector in (
        "button[type='submit']",
        "button[name='submit[Continue]']",
        "div[role='button'][aria-label*='Continue']",
        "div[role='button'][aria-label*='Submit']",
    ):
        btn = await page.query_selector(selector)
        if btn:
            await btn.click(timeout=5_000)
            return
    await page.press("body", "Enter")


async def stage_initiate(
    account_id: int,
    email: str,
    password: str,
    *,
    headless: bool,
    api_base: str,
    admin_user: str,
    admin_password: str,
) -> dict[str, Any]:
    state_file = _state_path(account_id)
    if state_file.exists():
        state_file.unlink()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport=VIEWPORT,
            locale=LOCALE,
            timezone_id=TIMEZONE,
        )
        page = await context.new_page()
        try:
            await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            await page.fill("input[name='email']", email, timeout=TIMEOUT_MS)
            await page.fill("input[name='pass']", password, timeout=TIMEOUT_MS)
            await _click_login(page)

            try:
                await page.wait_for_url(
                    lambda u: _classify_url(u) != "login",
                    timeout=POST_SUBMIT_WAIT_MS,
                )
            except PWTimeout:
                pass

            try:
                await page.wait_for_load_state("domcontentloaded", timeout=TIMEOUT_MS)
            except PWTimeout:
                pass

            url = page.url
            kind = _classify_url(url)

            if kind == "two_factor":
                await context.storage_state(path=str(state_file))
                return {
                    "status": "two_factor_pending",
                    "url": url,
                    "state_file": str(state_file),
                    "next": (
                        "Cek inbox email atau SMS HP, ambil kode 6-digit, "
                        "lalu jalanin: FB_LOGIN_OTP=<kode> python scripts/fb_auto_login.py continue"
                    ),
                }

            if kind == "checkpoint":
                return {"status": "checkpoint", "detail": f"checkpoint: {url}"}

            if kind == "login":
                return {"status": "failed", "detail": f"masih di login page: {url}"}

            if kind != "success":
                return {"status": "failed", "detail": f"unexpected url: {url}"}

            uploaded = await _upload_cookies(
                context, account_id, api_base, admin_user, admin_password
            )
            uploaded["url"] = url
            return uploaded
        finally:
            await context.close()
            await browser.close()


async def stage_continue(
    account_id: int,
    otp: str,
    *,
    headless: bool,
    api_base: str,
    admin_user: str,
    admin_password: str,
) -> dict[str, Any]:
    state_file = _state_path(account_id)
    if not state_file.exists():
        return {
            "status": "failed",
            "detail": f"state file {state_file} ga ada — jalanin stage initiate dulu",
        }

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(
            storage_state=str(state_file),
            user_agent=USER_AGENT,
            viewport=VIEWPORT,
            locale=LOCALE,
            timezone_id=TIMEZONE,
        )
        page = await context.new_page()
        try:
            # storage_state sudah include cookies + localStorage dari step 1.
            # Goto facebook.com — kalau FB tetep redirect ke 2FA, isi OTP di sana.
            await page.goto(
                "https://www.facebook.com/", wait_until="domcontentloaded", timeout=TIMEOUT_MS
            )

            url = page.url
            kind = _classify_url(url)

            if kind == "success":
                # 2FA udah ke-bypass somehow (e.g. trusted browser). Langsung harvest.
                uploaded = await _upload_cookies(
                    context, account_id, api_base, admin_user, admin_password
                )
                uploaded["url"] = url
                state_file.unlink(missing_ok=True)
                return uploaded

            if kind != "two_factor":
                return {
                    "status": "failed",
                    "detail": f"expected 2FA page, got: {kind} ({url})",
                }

            # Isi OTP. FB pake input[name='approvals_code'] di flow lama,
            # input[autocomplete='one-time-code'] di flow baru.
            otp_filled = False
            for selector in (
                "input[name='approvals_code']",
                "input[autocomplete='one-time-code']",
                "input[type='text'][maxlength='6']",
                "input[type='tel']",
                "input[type='text']",
            ):
                el = await page.query_selector(selector)
                if el:
                    await el.fill(otp, timeout=5_000)
                    otp_filled = True
                    break
            if not otp_filled:
                return {
                    "status": "failed",
                    "detail": "selector OTP input ga ketemu — DOM mungkin berubah",
                    "url": url,
                }

            await _click_submit_2fa(page)

            try:
                await page.wait_for_url(
                    lambda u: _classify_url(u) not in ("two_factor", "login"),
                    timeout=POST_SUBMIT_WAIT_MS,
                )
            except PWTimeout:
                pass

            try:
                await page.wait_for_load_state("domcontentloaded", timeout=TIMEOUT_MS)
            except PWTimeout:
                pass

            # FB sometimes punya intermediate page "Save browser?" (Yes/No).
            # Klik "Don't save" / "Continue" kalau ada, biar gak nyangkut.
            for selector in (
                "input[name='name_action_selected'][value='dont_save']",
                "button[name='submit[Continue]']",
                "button[type='submit']",
                "div[role='button'][aria-label*='Continue']",
            ):
                el = await page.query_selector(selector)
                if el:
                    try:
                        await el.click(timeout=3_000)
                        try:
                            await page.wait_for_load_state(
                                "domcontentloaded", timeout=10_000
                            )
                        except PWTimeout:
                            pass
                        break
                    except Exception:
                        continue

            url2 = page.url
            kind2 = _classify_url(url2)
            if kind2 != "success":
                return {
                    "status": "failed",
                    "detail": f"setelah submit OTP, masih di: {kind2} ({url2})",
                }

            uploaded = await _upload_cookies(
                context, account_id, api_base, admin_user, admin_password
            )
            uploaded["url"] = url2
            state_file.unlink(missing_ok=True)
            return uploaded
        finally:
            await context.close()
            await browser.close()


def _read_creds_from_env() -> tuple[str, str]:
    email = os.environ.get("FB_LOGIN_EMAIL", "").strip()
    password = os.environ.get("FB_LOGIN_PASSWORD", "")
    return email, password


def _read_creds_from_stdin() -> tuple[str, str]:
    email = input("FB email/phone: ").strip()
    password = getpass.getpass("FB password: ")
    return email, password


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage",
        choices=("initiate", "continue"),
        help="initiate = step 1 login dengan email+password; continue = step 2 submit OTP",
    )
    parser.add_argument("--account-id", type=int, default=int(os.environ.get("ACCOUNT_ID", "1")))
    parser.add_argument("--read-stdin", action="store_true")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--admin-user", default=os.environ.get("ADMIN_USER", "admin"))
    parser.add_argument("--admin-password", default=os.environ.get("ADMIN_PASSWORD", "admin123"))
    args = parser.parse_args()

    if args.stage == "initiate":
        if args.read_stdin:
            email, password = _read_creds_from_stdin()
        else:
            email, password = _read_creds_from_env()
        if not email or not password:
            print(json.dumps({"status": "failed", "detail": "missing email/password"}))
            return 2
        result = asyncio.run(
            stage_initiate(
                account_id=args.account_id,
                email=email,
                password=password,
                headless=not args.headed,
                api_base=args.api_base,
                admin_user=args.admin_user,
                admin_password=args.admin_password,
            )
        )
    else:
        otp = os.environ.get("FB_LOGIN_OTP", "").strip()
        if not otp:
            if args.read_stdin:
                otp = input("FB OTP code: ").strip()
        if not otp:
            print(json.dumps({"status": "failed", "detail": "missing FB_LOGIN_OTP"}))
            return 2
        result = asyncio.run(
            stage_continue(
                account_id=args.account_id,
                otp=otp,
                headless=not args.headed,
                api_base=args.api_base,
                admin_user=args.admin_user,
                admin_password=args.admin_password,
            )
        )

    print(json.dumps(result, indent=2))
    return 0 if result.get("status") in ("success", "two_factor_pending") else 1


if __name__ == "__main__":
    sys.exit(main())
