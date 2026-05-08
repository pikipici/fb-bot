"""Posts router."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from server.database import get_db

router = APIRouter()


@router.get("/posts")
async def list_posts(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """List collected posts with optional status filter."""
    # TODO: Query posts from DB
    return {"posts": [], "total": 0, "limit": limit, "offset": offset}
