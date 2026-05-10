"""Router — read-only trending posts feed + draft/skip status transitions.

``GET /api/v1/trending`` — list trending posts (any auth role).
``POST /api/v1/trending/{post_id}/draft`` — admin-only, render the active
template against the post and flip status to ``DRAFTED``. Returns the
rendered draft text for the UI to show in an editable textarea.
``POST /api/v1/trending/{post_id}/skip`` — admin-only, set status to
``SKIPPED``. Skip is a purely local action; FB is not touched.

Status transition rules for ``/draft``:
- ``NEW`` / ``DRAFTED`` / ``SKIPPED`` may be drafted (or re-drafted).
- ``COMMENTED`` is terminal; trying to draft it returns 409.
- Missing active template → 400.
- Missing post row → 404.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from server.auth import Role, get_current_user, require_role
from server.database import get_db
from server.models import Source, TrendingPost
from server.services.template_service import TemplateService, render_template

router = APIRouter(prefix="/trending", tags=["trending"])

_VALID_SORTS = {"score", "velocity", "recent"}
_VALID_STATUSES = {"NEW", "DRAFTED", "SKIPPED", "COMMENTED"}
_MAX_LIMIT = 200
_DEFAULT_LIMIT = 50

_admin_only = require_role(Role.ADMIN)


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


def _load_post_or_404(db: Session, post_id: int) -> TrendingPost:
    post = db.query(TrendingPost).filter(TrendingPost.id == post_id).first()
    if post is None:
        raise HTTPException(status_code=404, detail="Post gak ketemu")
    return post


@router.post("/{post_id}/draft")
def generate_draft(
    post_id: int,
    user=Depends(_admin_only),
    db: Session = Depends(get_db),
):
    """Render the active template and flip post to ``DRAFTED``.

    Allows re-drafting from ``NEW`` / ``DRAFTED`` / ``SKIPPED``, but
    rejects ``COMMENTED`` as terminal.
    """
    post = _load_post_or_404(db, post_id)
    if post.status == "COMMENTED":
        raise HTTPException(
            status_code=409,
            detail="Post udah COMMENTED, gak bisa di-draft ulang.",
        )

    template = TemplateService(db).get_active()
    if template is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Belum ada template aktif — isi dulu di halaman Template."
            ),
        )

    draft_text = render_template(
        template.template_text,
        author_name=post.author_name,
        text_snippet=post.text_snippet,
    )

    post.status = "DRAFTED"
    db.commit()
    db.refresh(post)

    source = (
        db.query(Source).filter(Source.id == post.source_id).first()
        if post.source_id is not None
        else None
    )
    return {"draft_text": draft_text, "post": _serialize(post, source)}


@router.post("/{post_id}/skip")
def skip_post(
    post_id: int,
    user=Depends(_admin_only),
    db: Session = Depends(get_db),
):
    """Mark post as ``SKIPPED`` locally (no FB interaction)."""
    post = _load_post_or_404(db, post_id)
    if post.status == "COMMENTED":
        raise HTTPException(
            status_code=409,
            detail="Post udah COMMENTED, gak bisa di-skip.",
        )
    post.status = "SKIPPED"
    db.commit()
    db.refresh(post)

    source = (
        db.query(Source).filter(Source.id == post.source_id).first()
        if post.source_id is not None
        else None
    )
    return {"post": _serialize(post, source)}
