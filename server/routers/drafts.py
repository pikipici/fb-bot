"""Drafts router."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from server.database import get_db
from server.auth import get_current_user

router = APIRouter()


@router.get("/drafts/pending")
async def list_pending_drafts(
    limit: int = 20,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """List drafts pending review."""
    # TODO: Query pending drafts
    return {"drafts": [], "total": 0, "limit": limit, "offset": offset}


@router.get("/drafts/{draft_id}")
async def get_draft(
    draft_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Get a specific draft with post context."""
    # TODO: Fetch draft + associated post
    return {"draft": None}
