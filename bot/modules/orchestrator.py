"""Orchestrator — runs the full cycle: filter → score → draft."""

import logging
from typing import Any

from sqlalchemy.orm import Session

from bot.modules.draft_engine import DraftEngine
from bot.modules.pipeline import Pipeline
from bot.services.draft_service import DraftService
from bot.services.post_service import PostService

logger = logging.getLogger(__name__)


class Orchestrator:
    """Coordinate the full processing cycle."""

    def __init__(self, db: Session, ai_enabled: bool = False):
        self.db = db
        self.ai_enabled = ai_enabled
        self.pipeline = Pipeline()
        self.draft_engine = DraftEngine()
        self.post_service = PostService(db)
        self.draft_service = DraftService(db)

    def process_collected_posts(self, raw_posts: list[dict[str, Any]]) -> dict[str, Any]:
        """Full cycle: filter → score → save → draft.

        Args:
            raw_posts: List of raw post dicts from collector (already parsed
                by ``Parser`` into the canonical shape with ``text_snippet``,
                ``fb_post_id``, engagement fields, etc).

        Returns:
            Summary of processing results. Contains ``queued`` (posts that
            passed the pipeline) and ``drafts_created`` (drafts produced
            successfully), among other counters.
        """
        if not raw_posts:
            return {
                "total_input": 0,
                "filtered": 0,
                "queued": 0,
                "saved_to_db": 0,
                "drafts_created": 0,
                "drafts_needs_manual": 0,
            }

        # Load existing IDs for duplicate detection (scoped by target when possible).
        target_id = raw_posts[0].get("target_id") if raw_posts else None
        existing_ids = self.post_service.get_existing_ids(target_id=target_id)
        self.pipeline.detector.load_seen_ids(existing_ids)

        # Step 1: Pipeline (filter + score). The pipeline reads ``text`` for
        # keyword matching; mirror ``text_snippet`` to ``text`` so parsed
        # posts from the collector work without the caller knowing.
        for p in raw_posts:
            if "text" not in p and "text_snippet" in p:
                p["text"] = p["text_snippet"]

        pipeline_result = self.pipeline.process_batch(raw_posts)

        # Step 2: Save queued posts to DB
        queued_posts = [
            p for p in pipeline_result["results"] if p["status"] == "QUEUED"
        ]
        saved_posts = self.post_service.save_batch(queued_posts)

        # Step 3: Generate drafts for saved posts
        drafts_created = 0
        drafts_manual = 0

        for post in saved_posts:
            post_dict = {
                "id": post.id,
                "text": post.text_snippet,
                "language": post.language,
            }
            draft_result = self.draft_engine.generate_draft(
                post_dict, ai_enabled=self.ai_enabled
            )
            draft_result["post_id"] = post.id

            self.draft_service.save_draft(draft_result)

            if draft_result["status"] == "PENDING_REVIEW":
                drafts_created += 1
            else:
                drafts_manual += 1

        # Reset fingerprints for next run
        self.draft_engine.reset_fingerprints()

        summary = {
            "total_input": pipeline_result["total"],
            "filtered": pipeline_result["filtered"],
            "queued": pipeline_result["queued"],
            "saved_to_db": len(saved_posts),
            "drafts_created": drafts_created,
            "drafts_needs_manual": drafts_manual,
        }

        logger.info("Orchestrator cycle complete: %s", summary)
        return summary
