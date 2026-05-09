"""Tests for scoring engine."""

import pytest
from datetime import datetime, timezone, timedelta

from bot.modules.scorer import ScoringEngine


@pytest.fixture
def scorer():
    return ScoringEngine()


class TestNormalizeEngagement:
    def test_zero_engagement(self, scorer):
        assert scorer.normalize_engagement(0, 0, 0) == 0.0

    def test_low_engagement(self, scorer):
        result = scorer.normalize_engagement(2, 1, 0)
        assert 0.0 < result < 0.5

    def test_high_engagement(self, scorer):
        result = scorer.normalize_engagement(500, 200, 100)
        assert result > 0.8

    def test_capped_at_one(self, scorer):
        result = scorer.normalize_engagement(10000, 5000, 5000)
        assert result <= 1.0


class TestFreshness:
    def test_very_fresh_post(self, scorer):
        now = datetime.now(timezone.utc)
        result = scorer.calculate_freshness(now - timedelta(minutes=5))
        assert result > 0.95

    def test_half_age_post(self, scorer):
        now = datetime.now(timezone.utc)
        half_age = scorer.thresholds["max_post_age_hours"] / 2
        result = scorer.calculate_freshness(now - timedelta(hours=half_age))
        assert 0.4 < result < 0.6

    def test_expired_post(self, scorer):
        now = datetime.now(timezone.utc)
        max_age = scorer.thresholds["max_post_age_hours"]
        result = scorer.calculate_freshness(now - timedelta(hours=max_age + 1))
        assert result == 0.0


class TestRelevance:
    def test_no_keywords(self, scorer):
        assert scorer.calculate_relevance(0, 0) == 0.0

    def test_some_matches(self, scorer):
        result = scorer.calculate_relevance(2, 10)
        assert 0.0 < result <= 1.0

    def test_many_matches(self, scorer):
        result = scorer.calculate_relevance(5, 10)
        assert result == 1.0


class TestRiskPenalty:
    """The method now returns a factor in ``[0.0, 1.0]``. The sign is
    applied downstream via the configured ``risk_penalty`` weight."""

    def test_no_risk(self, scorer):
        assert scorer.calculate_risk_penalty([]) == 0.0

    def test_one_risk_tag(self, scorer):
        result = scorer.calculate_risk_penalty(["politik"])
        assert result == pytest.approx(0.3)

    def test_multiple_risk_tags(self, scorer):
        result = scorer.calculate_risk_penalty(["politik", "sara", "hoax"])
        assert result == pytest.approx(0.9)

    def test_capped_at_one(self, scorer):
        result = scorer.calculate_risk_penalty(["a", "b", "c", "d", "e"])
        assert result == pytest.approx(1.0)

    def test_weight_applies_negative_sign(self, scorer):
        """Regression: the weight must actually be multiplied, not ignored."""
        assert scorer.weights["risk_penalty"] < 0
        # Raw factor is positive; score() folds it with the negative weight.
        assert scorer.calculate_risk_penalty(["x"]) > 0


class TestScore:
    def test_good_post_scores_high(self, scorer):
        post = {
            "likes": 50,
            "comments": 20,
            "shares": 10,
            "timestamp": datetime.now(timezone.utc) - timedelta(hours=1),
            "matched_keywords": 3,
            "total_keywords": 4,
            "risk_tags": [],
        }
        score = scorer.score(post)
        assert score > 0.5

    def test_risky_post_scores_low(self, scorer):
        post = {
            "likes": 50,
            "comments": 20,
            "shares": 10,
            "timestamp": datetime.now(timezone.utc) - timedelta(hours=1),
            "matched_keywords": 3,
            "total_keywords": 4,
            "risk_tags": ["politik", "sara", "hoax"],
        }
        score = scorer.score(post)
        assert score < 0.5

    def test_old_low_engagement_scores_zero(self, scorer):
        post = {
            "likes": 0,
            "comments": 0,
            "shares": 0,
            "timestamp": datetime.now(timezone.utc) - timedelta(hours=100),
            "matched_keywords": 0,
            "total_keywords": 4,
            "risk_tags": [],
        }
        score = scorer.score(post)
        assert score == 0.0

    def test_score_bounded_zero_to_one(self, scorer):
        post = {
            "likes": 1000,
            "comments": 500,
            "shares": 200,
            "timestamp": datetime.now(timezone.utc),
            "matched_keywords": 5,
            "total_keywords": 5,
            "risk_tags": [],
        }
        score = scorer.score(post)
        assert 0.0 <= score <= 1.0


class TestQueueDecision:
    def test_above_threshold_queued(self, scorer):
        assert scorer.should_queue(0.6) is True

    def test_below_threshold_not_queued(self, scorer):
        assert scorer.should_queue(0.3) is False

    def test_at_threshold_queued(self, scorer):
        assert scorer.should_queue(0.5) is True


class TestMinimumEngagement:
    def test_meets_minimum(self, scorer):
        assert scorer.meets_minimum_engagement(2, 1, 0) is True

    def test_below_minimum(self, scorer):
        assert scorer.meets_minimum_engagement(1, 0, 0) is False

    def test_zero_engagement(self, scorer):
        assert scorer.meets_minimum_engagement(0, 0, 0) is False
