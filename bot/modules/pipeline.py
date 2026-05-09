"""Pipeline — orchestrates detection, scoring, and queue decision."""

import logging
from typing import Any

from bot.modules.detector import Detector
from bot.modules.scorer import ScoringEngine

logger = logging.getLogger(__name__)


class Pipeline:
    """Process posts through filter → score → queue decision."""

    def __init__(
        self,
        scorer: ScoringEngine | None = None,
        detector: Detector | None = None,
    ):
        self.scorer = scorer or ScoringEngine()
        self.detector = detector or Detector()

    def process_post(self, post: dict[str, Any]) -> dict[str, Any]:
        """Process a single post through the full pipeline.

        Returns enriched post with score, status, and filter metadata.
        """
        # Step 1: Detection (keyword match, risk tag, language, duplicate)
        post = self.detector.detect(post)

        # Step 2: Minimum engagement check
        likes = post.get("likes", 0)
        comments = post.get("comments", 0)
        shares = post.get("shares", 0)

        if not self.scorer.meets_minimum_engagement(likes, comments, shares):
            post["status"] = "FILTERED_OUT"
            post["filter_reason"] = "low_engagement"
            post["score"] = 0.0
            logger.debug("Post %s filtered: low_engagement", post.get("fb_post_id"))
            return post

        # Step 3: Filter check
        filtered, reason = self.detector.should_filter_out(post)
        if filtered:
            post["status"] = "FILTERED_OUT"
            post["filter_reason"] = reason
            post["score"] = 0.0
            logger.debug("Post %s filtered: %s", post.get("fb_post_id"), reason)
            return post

        # Step 4: Score calculation
        score = self.scorer.score(post)
        post["score"] = score

        # Step 5: Queue decision
        if self.scorer.should_queue(score):
            post["status"] = "QUEUED"
            logger.info(
                "Post %s queued (score=%.4f)", post.get("fb_post_id"), score
            )
        else:
            post["status"] = "FILTERED_OUT"
            post["filter_reason"] = "below_threshold"
            logger.debug(
                "Post %s below threshold (score=%.4f)", post.get("fb_post_id"), score
            )

        return post

    def process_batch(self, posts: list[dict[str, Any]]) -> dict[str, Any]:
        """Process a batch of posts. Returns summary with results."""
        results = []
        queued = 0
        filtered = 0

        for post in posts:
            processed = self.process_post(post)
            results.append(processed)
            if processed["status"] == "QUEUED":
                queued += 1
            else:
                filtered += 1

        return {
            "results": results,
            "total": len(posts),
            "queued": queued,
            "filtered": filtered,
        }
