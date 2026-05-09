"""Drafts router."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from server.database import get_db
from server.auth import get_current_user
from server.models import Draft, Post

router = APIRouter()


@router.get("/drafts/pending")
async def list_pending_drafts(
    limit: int = 20,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """List drafts pending review."""
    query = db.query(Draft).filter(Draft.status == "PENDING_REVIEW")
    total = query.count()
    drafts = query.order_by(Draft.created_at.desc()).offset(offset).limit(limit).all()

    return {
        "drafts": [
            {
                "id": d.id,
                "post_id": d.post_id,
                "text": d.text,
                "source_type": d.source_type,
                "template_id": d.template_id,
                "status": d.status,
                "fingerprint": d.fingerprint,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in drafts
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/drafts/{draft_id}")
async def get_draft(
    draft_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Get a specific draft with post context."""
    draft = db.query(Draft).filter(Draft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    post = db.query(Post).filter(Post.id == draft.post_id).first()

    return {
        "draft": {
            "id": draft.id,
            "post_id": draft.post_id,
            "text": draft.text,
            "source_type": draft.source_type,
            "template_id": draft.template_id,
            "status": draft.status,
            "fingerprint": draft.fingerprint,
            "created_at": draft.created_at.isoformat() if draft.created_at else None,
        },
        "post": {
            "id": post.id,
            "fb_post_id": post.fb_post_id,
            "text_snippet": post.text_snippet,
            "likes": post.likes,
            "comments": post.comments,
            "shares": post.shares,
            "score": post.score,
            "language": post.language,
            "collected_at": post.collected_at.isoformat() if post.collected_at else None,
        } if post else None,
    }
