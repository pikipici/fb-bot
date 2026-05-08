"""Stats router."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from server.auth import get_current_user
from server.database import get_db

router = APIRouter()


@router.get("/stats/summary")
async def get_summary(
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Get summary statistics."""
    # TODO: Calculate stats from DB
    return {
        "posts_collected": 0,
        "posts_queued": 0,
        "drafts_pending": 0,
        "drafts_approved": 0,
        "drafts_rejected": 0,
        "targets_active": 0,
        "targets_degraded": 0,
    }
