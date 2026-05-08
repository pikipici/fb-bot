"""Approvals router."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from server.auth import get_current_user, require_role, Role
from server.database import get_db

router = APIRouter()


class ApprovalRequest(BaseModel):
    action: str  # approve, reject, edit
    reason: str | None = None
    edited_text: str | None = None


@router.post("/approvals/{draft_id}")
async def approve_draft(
    draft_id: int,
    request: ApprovalRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role(Role.OPERATOR, Role.ADMIN)),
):
    """Approve, reject, or edit a draft."""
    # TODO: Process approval action
    # TODO: Log to audit_logs
    return {"status": "ok", "draft_id": draft_id, "action": request.action}
