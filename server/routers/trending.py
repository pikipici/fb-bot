"""Router — read-only trending posts feed.

``GET /api/v1/trending`` — list trending posts surfaced by the scanner.

All authenticated roles (viewer / operator / admin) may read. Writes
are not exposed here; post status mutations happen through the
comment-draft flow in a later phase.

Query params:
- ``status`` — ``NEW`` | ``DRAFTED`` | ``SKIPPED`` | ``COMMENTED``. Default: no filter.
- ``source_id`` — filter by a specific source row.
- ``sort`` — ``score`` (default) | ``velocity`` | ``recent`` (collected_at desc).
- ``limit`` — max rows returned, default 50, hard capped at 200.

Response envelope::

    {
        "posts": [{...}],
        "total": <int>  # count of rows matching filters, independent of limit
    }
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from server.auth import get_current_user
from server.database import get_db
from server.models import Source, TrendingPost

router = APIRouter(prefix="/trending", tags=["trending"])

_VALID_SORTS = {"score", "velocity", "recent"}
_VALID_STATUSES = {"NEW", "DRAFTED", "SKIPPED", "COMMENTED"}
_MAX_LIMIT = 200
_DEFAULT_LIMIT = 50


def _serialize(post: TrendingPost, source: Source | None) -> dict:
    return {
        "id": post.id,
        "fb_post_id": post.fb_post_id,
        "author_name": post.author_name,
        "author_fb_id": post.author_fb_id,
        "text_snippet": post.text_snippet,
        "post_url": post.post_url,
        "thumbnail_url": post.thumbnail_url,
        "likes": post.likes,
        "comments": post.comments,
        "shares": post.shares,
        "reactions_total": post.reactions_total,
        "score": post.score,
        "velocity": post.velocity,
        "post_timestamp": (
            post.post_timestamp.isoformat() if post.post_timestamp else None
        ),
        "collected_at": (
            post.collected_at.isoformat() if post.collected_at else None
        ),
        "status": post.status,
        "source": (
            {
                "id": source.id,
                "type": source.type,
                "label": source.label,
            }
            if source is not None
            else None
        ),
    }


@router.get("")
def list_trending(
    status: str | None = None,
    source_id: int | None = None,
    sort: str = "score",
    limit: int = _DEFAULT_LIMIT,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if sort not in _VALID_SORTS:
        raise HTTPException(
            status_code=400,
            detail=f"sort harus salah satu dari {sorted(_VALID_SORTS)}",
        )
    if status is not None and status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"status harus salah satu dari {sorted(_VALID_STATUSES)}",
        )

    # Clamp limit to a sane upper bound. Values < 1 fall back to default
    # so the UI can't accidentally request zero rows.
    try:
        limit_int = int(limit)
    except (TypeError, ValueError):
        limit_int = _DEFAULT_LIMIT
    if limit_int < 1:
        limit_int = _DEFAULT_LIMIT
    if limit_int > _MAX_LIMIT:
        limit_int = _MAX_LIMIT

    query = db.query(TrendingPost)
    if status is not None:
        query = query.filter(TrendingPost.status == status)
    if source_id is not None:
        query = query.filter(TrendingPost.source_id == source_id)

    total = query.with_entities(func.count(TrendingPost.id)).scalar() or 0

    if sort == "velocity":
        query = query.order_by(desc(TrendingPost.velocity), desc(TrendingPost.id))
    elif sort == "recent":
        query = query.order_by(
            desc(TrendingPost.collected_at), desc(TrendingPost.id)
        )
    else:  # "score"
        query = query.order_by(desc(TrendingPost.score), desc(TrendingPost.id))

    rows = query.limit(limit_int).all()

    source_ids = {row.source_id for row in rows}
    sources_by_id: dict[int, Source] = {}
    if source_ids:
        for src in db.query(Source).filter(Source.id.in_(source_ids)).all():
            sources_by_id[src.id] = src

    posts = [
        _serialize(row, sources_by_id.get(row.source_id)) for row in rows
    ]
    return {"posts": posts, "total": int(total)}
