"""Router — FB Account management (admin only)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from server.auth import Role, require_role
from server.database import get_db
from server.services.cookie_session_service import (
    CookieValidationError,
    parse_cookie_string,
    validate_and_fetch_profile,
)
from server.services.fb_account_service import FBAccountService

router = APIRouter(prefix="/fb-accounts", tags=["fb-accounts"])


_admin_only = require_role(Role.ADMIN)


class CreateAccountRequest(BaseModel):
    label: str
    email: str
    password: str
    purpose: str = "both"  # scrape, post, both
    notes: str = ""


class UpdateAccountRequest(BaseModel):
    label: str | None = None
    email: str | None = None
    password: str | None = None
    purpose: str | None = None
    notes: str | None = None
    status: str | None = None


class PreviewCookieRequest(BaseModel):
    raw_cookies: str


class ConnectCookieRequest(BaseModel):
    label: str
    raw_cookies: str
    notes: str = ""


@router.get("")
def list_accounts(
    include_disabled: bool = False,
    user=Depends(_admin_only),
    db: Session = Depends(get_db),
):
    """List all FB accounts (admin only). Emails shown, passwords never."""
    svc = FBAccountService(db)
    accounts = svc.list_accounts(include_disabled=include_disabled)
    return {
        "accounts": [svc.to_dict(a, include_email=True) for a in accounts],
        "total": len(accounts),
    }


@router.get("/current")
def get_current_account(
    user=Depends(_admin_only),
    db: Session = Depends(get_db),
):
    """Return the single managed FB account (or null when none exists).

    FB Bot is single-account by design; this endpoint is the canonical read
    for the setup/edit UI.
    """
    svc = FBAccountService(db)
    accounts = svc.list_accounts(include_disabled=True)
    if not accounts:
        return {"account": None}
    return {"account": svc.to_dict(accounts[0], include_email=True)}


@router.post("", status_code=201)
def create_account(
    req: CreateAccountRequest,
    user=Depends(_admin_only),
    db: Session = Depends(get_db),
):
    """Add the FB account (admin only). Single-account system — rejects with
    HTTP 409 if an account already exists (active or not)."""
    if req.purpose not in ("scrape", "post", "both"):
        raise HTTPException(400, "purpose must be 'scrape', 'post', or 'both'")

    svc = FBAccountService(db)
    existing = svc.list_accounts(include_disabled=True)
    if existing:
        raise HTTPException(
            409,
            "An FB account already exists. Edit or delete the existing one instead.",
        )

    account = svc.create_account(
        label=req.label,
        email=req.email,
        password=req.password,
        purpose=req.purpose,
        notes=req.notes,
    )
    return svc.to_dict(account, include_email=True)


@router.get("/{account_id}")
def get_account(
    account_id: int,
    user=Depends(_admin_only),
    db: Session = Depends(get_db),
):
    """Get a single FB account detail (admin only)."""
    svc = FBAccountService(db)
    account = svc.get_account(account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    return svc.to_dict(account, include_email=True)


@router.put("/{account_id}")
def update_account(
    account_id: int,
    req: UpdateAccountRequest,
    user=Depends(_admin_only),
    db: Session = Depends(get_db),
):
    """Update FB account (admin only)."""
    svc = FBAccountService(db)
    account = svc.update_account(
        account_id,
        label=req.label,
        email=req.email,
        password=req.password,
        purpose=req.purpose,
        notes=req.notes,
        status=req.status,
    )
    if not account:
        raise HTTPException(404, "Account not found")
    return svc.to_dict(account, include_email=True)


@router.delete("/{account_id}")
def delete_account(
    account_id: int,
    user=Depends(_admin_only),
    db: Session = Depends(get_db),
):
    """Delete FB account permanently (admin only)."""
    svc = FBAccountService(db)
    if not svc.delete_account(account_id):
        raise HTTPException(404, "Account not found")
    return {"status": "deleted", "id": account_id}


@router.post("/{account_id}/reactivate")
def reactivate_account(
    account_id: int,
    user=Depends(_admin_only),
    db: Session = Depends(get_db),
):
    """Reactivate a blocked/disabled account (admin only)."""
    svc = FBAccountService(db)
    if not svc.reactivate(account_id):
        raise HTTPException(404, "Account not found")
    account = svc.get_account(account_id)
    return svc.to_dict(account, include_email=True)


@router.post("/{account_id}/re-validate")
async def re_validate_account(
    account_id: int,
    user=Depends(_admin_only),
    db: Session = Depends(get_db),
):
    """Dry-run cookie re-check for a stored cookie-connected account.

    Decrypts the stored cookie, hits ``m.facebook.com`` via the same
    validator used at connect time, then flips the account status
    without mutating the stored cookie payload itself.

    Returns ``{valid: bool, account: dict}``.

    * ``200 valid=True``  — cookie passes, account flipped to ACTIVE,
      ``cookies_expired_at`` cleared, profile fields refreshed from the
      validator response (FB display name / avatar may have changed).
    * ``200 valid=False`` — validator raised
      :class:`CookieValidationError`; account flipped to EXPIRED,
      ``cookies_expired_at`` stamped.
    * ``400`` — account has no stored cookie (manual creds only).
    * ``404`` — account does not exist.
    """
    svc = FBAccountService(db)
    account = svc.get_account(account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    if not account.cookies_encrypted:
        raise HTTPException(
            400,
            "Akun ini pakai kredensial manual, gak ada cookie yang bisa dicek",
        )

    cookies = svc.get_cookies(account_id)
    if not cookies:
        # Defensive — cookies_encrypted was truthy but decrypt yielded
        # nothing. Treat as corrupted session so admin can re-upload.
        svc.mark_cookies_expired(account_id)
        refreshed = svc.get_account(account_id)
        return {
            "valid": False,
            "account": svc.to_dict(refreshed, include_email=True),
        }

    try:
        profile = await validate_and_fetch_profile(cookies)
    except CookieValidationError:
        svc.mark_cookies_expired(account_id)
        refreshed = svc.get_account(account_id)
        return {
            "valid": False,
            "account": svc.to_dict(refreshed, include_email=True),
        }

    svc.mark_active_from_profile(
        account_id,
        fb_user_id=profile.fb_user_id,
        fb_name=profile.name,
        fb_profile_pic_url=profile.profile_pic_url,
    )
    refreshed = svc.get_account(account_id)
    return {
        "valid": True,
        "account": svc.to_dict(refreshed, include_email=True),
    }


# --- Cookie-session endpoints (Layer 1+2) --------------------------------


@router.post("/preview-cookie")
async def preview_cookie(
    req: PreviewCookieRequest,
    user=Depends(_admin_only),
):
    """Validate a raw cookie string and return the profile it belongs to.

    Does NOT persist anything — it's a dry-run so the dashboard can show
    the user a confirmation card before committing. The actual save
    happens via ``POST /fb-accounts/connect-cookie``.
    """
    if not req.raw_cookies or not req.raw_cookies.strip():
        raise HTTPException(400, "raw_cookies kosong")

    cookies = parse_cookie_string(req.raw_cookies)
    try:
        profile = await validate_and_fetch_profile(cookies)
    except CookieValidationError as exc:
        raise HTTPException(400, str(exc)) from exc

    return {
        "ok": True,
        "preview": {
            "fb_user_id": profile.fb_user_id,
            "name": profile.name,
            "profile_pic_url": profile.profile_pic_url,
        },
    }


@router.post("/connect-cookie", status_code=201)
async def connect_cookie(
    req: ConnectCookieRequest,
    user=Depends(_admin_only),
    db: Session = Depends(get_db),
):
    """Persist a cookie-connected FB account.

    Flow:
      1. Enforce single-account (409 if one already exists).
      2. Parse + validate cookies via ``m.facebook.com/me``.
      3. Encrypt cookies with Fernet and save together with the profile
         info returned by Facebook.

    Cookies are never returned in the response, only the public profile
    fields ``fb_user_id`` / ``fb_name`` / ``fb_profile_pic_url``.
    """
    if not req.label or not req.label.strip():
        raise HTTPException(400, "label kosong")
    if not req.raw_cookies or not req.raw_cookies.strip():
        raise HTTPException(400, "raw_cookies kosong")

    svc = FBAccountService(db)
    existing = svc.list_accounts(include_disabled=True)
    if existing:
        raise HTTPException(
            409,
            "An FB account already exists. Delete the existing one first.",
        )

    cookies = parse_cookie_string(req.raw_cookies)
    try:
        profile = await validate_and_fetch_profile(cookies)
    except CookieValidationError as exc:
        raise HTTPException(400, str(exc)) from exc

    account = svc.create_cookie_account(
        label=req.label.strip(),
        cookies=cookies,
        fb_user_id=profile.fb_user_id,
        fb_name=profile.name,
        fb_profile_pic_url=profile.profile_pic_url,
        notes=req.notes,
    )
    return svc.to_dict(account, include_email=True)
