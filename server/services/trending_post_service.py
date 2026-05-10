"""Trending post service — upsert scanned posts into ``trending_posts``.

Pipeline per post (one call to :meth:`upsert`):
1. Apply source keyword filter (include/exclude) against the post text.
2. Compute :class:`TrendingScore`. If ``is_trending`` is false, skip.
3. Upsert by ``fb_post_id``:
   - new row → insert with ``status='NEW'``
   - existing row → refresh metrics, score, velocity, but **preserve**
     ``status`` when it's not ``NEW`` (user already acted on it).

This keeps scans idempotent and safe to repeat every 15 minutes without
clobbering user intent from the review UI.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy.orm import Session

from bot.modules.keyword_filter import matches_keyword_filter
from bot.modules.trending_scorer import score_trending
from server.models import Source, TrendingPost
from server.services.source_service import _decode_keywords
from server.utils.fb_url import classify_unsupported_post_url

logger = logging.getLogger(__name__)

# Status values we treat as "user has acted; don't downgrade back to NEW".
_USER_OWNED_STATUSES = frozenset({"DRAFTED", "COMMENTED", "SKIPPED"})


@dataclass
class UpsertResult:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0

    def merge(self, other: "UpsertResult") -> "UpsertResult":
        return UpsertResult(
            inserted=self.inserted + other.inserted,
            updated=self.updated + other.updated,
            skipped=self.skipped + other.skipped,
        )


def _coerce_timestamp(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


class TrendingPostService:
    """Gateway between the scanner and the ``trending_posts`` table."""

    def __init__(self, db: Session):
        self.db = db

    # --- upsert --------------------------------------------------------

    def upsert(
        self,
        source_id: int,
        post: dict[str, Any],
    ) -> UpsertResult:
        source = (
            self.db.query(Source).filter(Source.id == source_id).first()
        )
        if source is None:
            logger.warning("upsert: source %d gak ketemu, skip", source_id)
            return UpsertResult(skipped=1)

        text = post.get("text") or post.get("text_snippet") or ""
        include = _decode_keywords(source.keywords_include)
        exclude = _decode_keywords(source.keywords_exclude)

        if not matches_keyword_filter(text, include=include, exclude=exclude):
            return UpsertResult(skipped=1)

        # Drop Stories / Reels / Watch URLs — they have no comment
        # composer DOM, so letting them into the table just creates dead
        # cards that can only be Skipped. Missing post_url is still
        # allowed (upstream logic handles nulls).
        post_url = post.get("post_url")
        if post_url and classify_unsupported_post_url(post_url) is not None:
            logger.debug(
                "upsert: dropping unsupported URL shape at source=%d (%s)",
                source_id,
                post_url,
            )
            return UpsertResult(skipped=1)

        scored = score_trending(post)
        if not scored.is_trending:
            return UpsertResult(skipped=1)

        fb_post_id = post.get("fb_post_id")
        if not fb_post_id:
            logger.warning(
                "upsert: post tanpa fb_post_id di source %d, skip", source_id
            )
            return UpsertResult(skipped=1)

        existing = (
            self.db.query(TrendingPost)
            .filter(TrendingPost.fb_post_id == fb_post_id)
            .first()
        )

        likes = int(post.get("likes") or 0)
        comments = int(post.get("comments") or 0)
        shares = int(post.get("shares") or 0)
        reactions_total = int(
            post.get("reactions_total") or (likes + comments + shares)
        )
        post_timestamp = _coerce_timestamp(post.get("post_timestamp"))

        if existing is None:
            row = TrendingPost(
                fb_post_id=fb_post_id,
                source_id=source_id,
                author_name=post.get("author_name"),
                author_fb_id=post.get("author_fb_id"),
                text_snippet=text[:500] if text else None,
                post_url=post.get("post_url"),
                thumbnail_url=post.get("thumbnail_url"),
                likes=likes,
                comments=comments,
                shares=shares,
                reactions_total=reactions_total,
                score=scored.score,
                velocity=scored.velocity,
                post_timestamp=post_timestamp,
                status="NEW",
            )
            self.db.add(row)
            self.db.commit()
            return UpsertResult(inserted=1)

        # Refresh metrics but preserve user status.
        existing.author_name = post.get("author_name") or existing.author_name
        existing.author_fb_id = (
            post.get("author_fb_id") or existing.author_fb_id
        )
        existing.text_snippet = (
            text[:500] if text else existing.text_snippet
        )
        existing.post_url = post.get("post_url") or existing.post_url
        existing.thumbnail_url = (
            post.get("thumbnail_url") or existing.thumbnail_url
        )
        existing.likes = likes
        existing.comments = comments
        existing.shares = shares
        existing.reactions_total = reactions_total
        existing.score = scored.score
        existing.velocity = scored.velocity
        if post_timestamp is not None:
            existing.post_timestamp = post_timestamp
        # Only reset to NEW if nothing happened yet.
        if existing.status not in _USER_OWNED_STATUSES:
            existing.status = "NEW"
        self.db.commit()
        return UpsertResult(updated=1)

    def upsert_batch(
        self,
        source_id: int,
        posts: Iterable[dict[str, Any]],
    ) -> UpsertResult:
        total = UpsertResult()
        for post in posts:
            total = total.merge(self.upsert(source_id, post))
        return total

    # --- read ---------------------------------------------------------

    def list_trending(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TrendingPost]:
        query = self.db.query(TrendingPost)
        if status:
            query = query.filter(TrendingPost.status == status)
        return (
            query.order_by(TrendingPost.score.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
