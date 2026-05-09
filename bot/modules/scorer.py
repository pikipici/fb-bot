"""Scoring Engine — calculates priority score for posts."""

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ScoringEngine:
    """Calculate priority scores for collected posts."""

    def __init__(self, config_path: str | None = None):
        if config_path is None:
            config_path = str(
                Path(__file__).parent.parent / "config" / "scoring_rules.json"
            )
        with open(config_path) as f:
            self.config = json.load(f)

        self.weights = self.config["weights"]
        self.thresholds = self.config["thresholds"]

    def normalize_engagement(self, likes: int, comments: int, shares: int) -> float:
        """Normalize engagement using log scale."""
        total = likes + comments + shares
        if total <= 0:
            return 0.0
        return min(math.log(total + 1) / math.log(1000), 1.0)

    def calculate_freshness(self, post_timestamp: Any) -> float:
        """Calculate freshness score (0-1). Newer = higher.

        Accepts ``datetime`` or ISO-8601 string (parser produces the
        latter). Naive datetimes/strings are assumed UTC. Unparseable or
        missing input returns ``0.0`` rather than raising.
        """
        if post_timestamp is None:
            return 0.0

        if isinstance(post_timestamp, str):
            try:
                post_timestamp = datetime.fromisoformat(
                    post_timestamp.replace("Z", "+00:00")
                )
            except ValueError:
                return 0.0

        if not isinstance(post_timestamp, datetime):
            return 0.0

        if post_timestamp.tzinfo is None:
            post_timestamp = post_timestamp.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        age_hours = (now - post_timestamp).total_seconds() / 3600
        max_age = self.thresholds["max_post_age_hours"]
        if age_hours >= max_age:
            return 0.0
        return max(0.0, 1.0 - (age_hours / max_age))

    def calculate_relevance(self, matched_keywords: int, total_keywords: int) -> float:
        """Calculate relevance score based on keyword matches."""
        if total_keywords == 0:
            return 0.0
        return min(matched_keywords / max(total_keywords * 0.3, 1), 1.0)

    def calculate_risk_penalty(self, risk_tags: list[str]) -> float:
        """Return a scalar in ``[-1.0, 0.0]`` representing the risk factor.

        The factor is scaled to [0, 1] (0.3 per tag, capped at 1) and the
        sign is applied downstream by multiplying with the configured
        ``risk_penalty`` weight (negative). Previously the penalty was
        returned pre-signed and added raw to the score, which made the
        weight in ``scoring_rules.json`` effectively dead.
        """
        if not risk_tags:
            return 0.0
        return min(len(risk_tags) * 0.3, 1.0)

    def score(self, post: dict[str, Any]) -> float:
        """Calculate final score for a post."""
        engagement = self.normalize_engagement(
            post.get("likes", 0),
            post.get("comments", 0),
            post.get("shares", 0),
        )
        freshness = self.calculate_freshness(post["timestamp"])
        relevance = self.calculate_relevance(
            post.get("matched_keywords", 0),
            post.get("total_keywords", 1),
        )
        risk_factor = self.calculate_risk_penalty(post.get("risk_tags", []))

        score = (
            self.weights["engagement"] * engagement
            + self.weights["freshness"] * freshness
            + self.weights["relevance"] * relevance
            + self.weights["risk_penalty"] * risk_factor
        )
        return round(max(0.0, min(1.0, score)), 4)

    def should_queue(self, score: float) -> bool:
        """Determine if a post should enter the review queue."""
        return score >= self.thresholds["queue_score_min"]

    def meets_minimum_engagement(
        self, likes: int, comments: int, shares: int
    ) -> bool:
        """Check if post meets minimum engagement threshold."""
        total = likes + comments + shares
        return total >= self.thresholds["min_engagement"]
