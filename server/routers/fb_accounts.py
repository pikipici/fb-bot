"""Router — FB Account management (admin only)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from server.auth import Role, require_role
from server.database import get_db
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
