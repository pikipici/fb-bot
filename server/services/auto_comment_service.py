"""Auto-Comment Service — orchestrate eligible TrendingPost selection.

Phase K-1 — pick the next post that should get an auto-generated comment.

Eligibility rules (intentionally strict — dedup is a hard requirement so we
don't double-comment if the chain reschedules over an in-flight tick):

1. ``TrendingPost.status == 'NEW'`` (NOT yet COMMENTED/SKIPPED/DRAFTED)
2. No ``CommentHistory`` row exists for the post (any status — SENT/FAILED/
   PENDING all count as "already attempted, never retry").
3. Order by ``collected_at ASC`` (oldest first FIFO).

If every candidate has been touched already, returns ``None`` and the caller
reschedules normally.

Usage::

    svc = AutoCommentService(db)
    post = svc.pick_next_eligible_post()
    if post is None:
        # nothing fresh, just reschedule
        return
    # ... pipeline ...
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.models import CommentHistory, TrendingPost

logger = logging.getLogger(__name__)


class AutoCommentService:
    """Eligibility + orchestration helpers for the auto-comment pipeline."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def pick_next_eligible_post(self) -> TrendingPost | None:
        """Return the oldest fresh ``TrendingPost`` that has never been
        touched by a comment attempt, or ``None`` if no candidates exist.
        """
        already_attempted = select(CommentHistory.trending_post_id)

        stmt = (
            select(TrendingPost)
            .where(TrendingPost.status == "NEW")
            .where(~TrendingPost.id.in_(already_attempted))
            .order_by(TrendingPost.collected_at.asc())
            .limit(1)
        )

        return self.db.execute(stmt).scalars().first()
