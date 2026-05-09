"""Stats router."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from server.auth import get_current_user
from server.database import get_db
from server.models import Post, Draft, Target

router = APIRouter()


@router.get("/stats/summary")
async def get_summary(
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Get summary statistics."""
    posts_collected = db.query(Post).count()
    posts_queued = db.query(Post).filter(Post.status == "QUEUED").count()
    drafts_pending = db.query(Draft).filter(Draft.status == "PENDING_REVIEW").count()
    drafts_approved = db.query(Draft).filter(Draft.status == "APPROVED").count()
    drafts_rejected = db.query(Draft).filter(Draft.status == "REJECTED").count()
    targets_active = db.query(Target).filter(Target.health_status == "ACTIVE").count()
    targets_degraded = db.query(Target).filter(Target.health_status == "DEGRADED").count()

    return {
        "posts_collected": posts_collected,
        "posts_queued": posts_queued,
        "drafts_pending": drafts_pending,
        "drafts_approved": drafts_approved,
        "drafts_rejected": drafts_rejected,
        "targets_active": targets_active,
        "targets_degraded": targets_degraded,
    }
