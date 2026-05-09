"""Post service — database operations for posts."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from server.models import Post


class PostService:
    """Handle post CRUD and query operations."""

    def __init__(self, db: Session):
        self.db = db

    def save_post(self, post_data: dict[str, Any]) -> Post:
        """Save a processed post to the database."""
        post = Post(
            fb_post_id=post_data["fb_post_id"],
            target_id=post_data.get("target_id", ""),
            url=post_data.get("url"),
            author_id=post_data.get("author_id"),
            text_snippet=post_data.get("text", "")[:500],
            language=post_data.get("language", "id"),
            likes=post_data.get("likes", 0),
            comments=post_data.get("comments", 0),
            shares=post_data.get("shares", 0),
            score=post_data.get("score", 0.0),
            status=post_data.get("status", "QUEUED"),
            collected_at=datetime.now(timezone.utc),
            post_timestamp=post_data.get("timestamp"),
        )
        self.db.add(post)
        self.db.commit()
        self.db.refresh(post)
        return post

    def save_batch(self, posts: list[dict[str, Any]]) -> list[Post]:
        """Save a batch of processed posts."""
        saved = []
        for post_data in posts:
            # Skip duplicates at DB level
            if self.is_duplicate(post_data["fb_post_id"]):
                continue
            saved.append(self.save_post(post_data))
        return saved

    def is_duplicate(self, fb_post_id: str) -> bool:
        """Check if a post already exists in the database."""
        return (
            self.db.query(Post).filter(Post.fb_post_id == fb_post_id).first()
            is not None
        )

    def get_existing_ids(self, target_id: str | None = None) -> list[str]:
        """Get all existing fb_post_ids, optionally filtered by target."""
        query = self.db.query(Post.fb_post_id)
        if target_id:
            query = query.filter(Post.target_id == target_id)
        return [row[0] for row in query.all()]

    def get_queued_posts(self, limit: int = 50) -> list[Post]:
        """Get posts with QUEUED status for draft generation."""
        return (
            self.db.query(Post)
            .filter(Post.status == "QUEUED")
            .order_by(Post.score.desc())
            .limit(limit)
            .all()
        )

    def update_status(self, post_id: int, status: str):
        """Update post status."""
        post = self.db.query(Post).filter(Post.id == post_id).first()
        if post:
            post.status = status
            self.db.commit()
