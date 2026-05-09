"""Draft service — database operations for drafts and approvals."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from server.models import Draft, Approval, AuditLog


class DraftService:
    """Handle draft CRUD and approval operations."""

    def __init__(self, db: Session):
        self.db = db

    def save_draft(self, draft_data: dict[str, Any]) -> Draft:
        """Save a generated draft to the database."""
        draft = Draft(
            post_id=draft_data["post_id"],
            text=draft_data.get("text"),
            source_type=draft_data.get("source_type", "static"),
            template_id=draft_data.get("template_id"),
            status=draft_data.get("status", "PENDING_REVIEW"),
            fingerprint=draft_data.get("fingerprint"),
            created_at=datetime.now(timezone.utc),
        )
        self.db.add(draft)
        self.db.commit()
        self.db.refresh(draft)
        return draft

    def get_pending_drafts(self, limit: int = 20, offset: int = 0) -> tuple[list[Draft], int]:
        """Get drafts pending review with total count."""
        query = self.db.query(Draft).filter(Draft.status == "PENDING_REVIEW")
        total = query.count()
        drafts = query.order_by(Draft.created_at.desc()).offset(offset).limit(limit).all()
        return drafts, total

    def get_draft_by_id(self, draft_id: int) -> Draft | None:
        """Get a specific draft."""
        return self.db.query(Draft).filter(Draft.id == draft_id).first()

    def approve_draft(self, draft_id: int, user_id: int, edited_text: str | None = None) -> Draft | None:
        """Approve a draft, optionally with edits."""
        draft = self.get_draft_by_id(draft_id)
        if not draft:
            return None

        draft.status = "APPROVED"
        if edited_text:
            draft.text = edited_text

        # Create approval record
        approval = Approval(
            draft_id=draft_id,
            user_id=user_id,
            action="approve",
            edited_text=edited_text,
            created_at=datetime.now(timezone.utc),
        )
        self.db.add(approval)

        # Audit log
        self._log_action(user_id, "approve_draft", "draft", str(draft_id))

        self.db.commit()
        self.db.refresh(draft)
        return draft

    def reject_draft(self, draft_id: int, user_id: int, reason: str | None = None) -> Draft | None:
        """Reject a draft with optional reason."""
        draft = self.get_draft_by_id(draft_id)
        if not draft:
            return None

        draft.status = "REJECTED"

        approval = Approval(
            draft_id=draft_id,
            user_id=user_id,
            action="reject",
            reason=reason,
            created_at=datetime.now(timezone.utc),
        )
        self.db.add(approval)

        self._log_action(user_id, "reject_draft", "draft", str(draft_id), reason)

        self.db.commit()
        self.db.refresh(draft)
        return draft

    def get_draft_stats(self) -> dict[str, int]:
        """Get draft statistics."""
        pending = self.db.query(Draft).filter(Draft.status == "PENDING_REVIEW").count()
        approved = self.db.query(Draft).filter(Draft.status == "APPROVED").count()
        rejected = self.db.query(Draft).filter(Draft.status == "REJECTED").count()
        manual = self.db.query(Draft).filter(Draft.status == "NEEDS_MANUAL_WRITE").count()
        return {
            "pending": pending,
            "approved": approved,
            "rejected": rejected,
            "needs_manual": manual,
        }

    def _log_action(
        self, user_id: int, action: str, resource_type: str, resource_id: str, details: str | None = None
    ):
        """Write to audit log."""
        log = AuditLog(
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            created_at=datetime.now(timezone.utc),
        )
        self.db.add(log)
