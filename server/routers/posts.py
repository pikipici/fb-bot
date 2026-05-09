"""Posts router."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from server.database import get_db
from server.auth import get_current_user
from server.models import Post

router = APIRouter()


@router.get("/posts")
async def list_posts(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """List collected posts with optional status filter."""
    query = db.query(Post)
    if status:
        query = query.filter(Post.status == status)

    total = query.count()
    posts = query.order_by(Post.score.desc()).offset(offset).limit(limit).all()

    return {
        "posts": [
            {
                "id": p.id,
                "fb_post_id": p.fb_post_id,
                "target_id": p.target_id,
                "text_snippet": p.text_snippet,
                "language": p.language,
                "likes": p.likes,
                "comments": p.comments,
                "shares": p.shares,
                "score": p.score,
                "status": p.status,
                "collected_at": p.collected_at.isoformat() if p.collected_at else None,
            }
            for p in posts
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
