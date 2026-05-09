"""Approvals router."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from server.auth import get_current_user, require_role, Role
from server.database import get_db
from server.models import Draft, Approval, AuditLog

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
    draft = db.query(Draft).filter(Draft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    if draft.status != "PENDING_REVIEW":
        raise HTTPException(status_code=400, detail="Draft is not pending review")

    user_id = int(user.get("sub", 0))

    # Update draft status
    if request.action == "approve":
        draft.status = "APPROVED"
        if request.edited_text:
            draft.text = request.edited_text
    elif request.action == "reject":
        draft.status = "REJECTED"
    else:
        raise HTTPException(status_code=400, detail="Invalid action. Use 'approve' or 'reject'")

    # Create approval record
    approval = Approval(
        draft_id=draft_id,
        user_id=user_id,
        action=request.action,
        reason=request.reason,
        edited_text=request.edited_text,
        created_at=datetime.now(timezone.utc),
    )
    db.add(approval)

    # Audit log
    audit = AuditLog(
        user_id=user_id,
        action=f"{request.action}_draft",
        resource_type="draft",
        resource_id=str(draft_id),
        details=request.reason,
        created_at=datetime.now(timezone.utc),
    )
    db.add(audit)

    db.commit()

    return {"status": "ok", "draft_id": draft_id, "action": request.action, "new_status": draft.status}
