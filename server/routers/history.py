"""Router — read-only comment history feed (Layer 2 audit trail).

``GET /api/v1/history`` — any authenticated user (viewer or admin).
Returns ``comment_history`` rows ordered by ``sent_at DESC`` with
denormalised post summary for UI rendering without an extra fetch.

Query params:
- ``status`` (optional) — filter by ``SENT`` | ``FAILED`` | ``PENDING``.
  Invalid values return 400.
- ``limit`` (default 50, max 200).
- ``offset`` (default 0).

Response envelope::

    {
      "items": [
        {
          "id": 12,
          "trending_post_id": 3,
          "comment_text": "halo bro",
          "status": "SENT",
          "fb_comment_id": "c_abc" | null,
          "error_message": null,
          "sent_at": "2026-05-10T12:34:56+00:00",
          "post": {
            "author_name": "Alice",
            "text_snippet": "...",
            "post_url": "https://fb.com/..." | null,
            "thumbnail_url": null,
            "status": "COMMENTED"
          }
        }
      ],
      "total": 47
    }
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from server.auth import get_current_user
from server.database import get_db
from server.models import CommentHistory, TrendingPost

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/history", tags=["history"])

_VALID_STATUSES = {"SENT", "FAILED", "PENDING"}
_MAX_LIMIT = 200
_DEFAULT_LIMIT = 50


def _serialize(history: CommentHistory, post: TrendingPost | None) -> dict:
    return {
        "id": history.id,
        "trending_post_id": history.trending_post_id,
        "user_id": history.user_id,
        "comment_text": history.comment_text,
        "fb_comment_id": history.fb_comment_id,
        "status": history.status,
        "error_message": history.error_message,
        "sent_at": (
            history.sent_at.isoformat() if history.sent_at else None
        ),
        "post": (
            {
                "id": post.id,
                "fb_post_id": post.fb_post_id,
                "author_name": post.author_name,
                "text_snippet": post.text_snippet,
                "post_url": post.post_url,
                "thumbnail_url": post.thumbnail_url,
                "status": post.status,
            }
            if post is not None
            else None
        ),
    }


@router.get("")
def list_history(
    status: str | None = Query(default=None),
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return ``comment_history`` rows with denormalised post summary."""
    if status is not None and status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"status harus salah satu dari "
                f"{sorted(_VALID_STATUSES)} — dapet '{status}'"
            ),
        )

    base = db.query(CommentHistory)
    if status is not None:
        base = base.filter(CommentHistory.status == status)

    total = base.with_entities(func.count(CommentHistory.id)).scalar() or 0

    rows = (
        base.order_by(desc(CommentHistory.sent_at), desc(CommentHistory.id))
        .offset(offset)
        .limit(limit)
        .all()
    )

    # Batch-load posts so we don't N+1 per row.
    post_ids = {r.trending_post_id for r in rows}
    posts_by_id: dict[int, TrendingPost] = {}
    if post_ids:
        for p in (
            db.query(TrendingPost)
            .filter(TrendingPost.id.in_(post_ids))
            .all()
        ):
            posts_by_id[p.id] = p

    items = [
        _serialize(r, posts_by_id.get(r.trending_post_id)) for r in rows
    ]
    return {"items": items, "total": int(total)}
