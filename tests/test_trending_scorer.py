"""Tests for trending_scorer — velocity + absolute-reactions heuristic.

Separate from ``scorer.py``/``ScoringEngine`` (which feeds the old
review-queue pipeline). The trending scorer is purpose-built for the
Layer-1 scanner: does this post deserve to show up on ``/trending``?
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bot.modules.trending_scorer import (
    TrendingScore,
    score_trending,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- score_trending() ----------------------------------------------------


class TestScoreTrendingReturn:
    def test_returns_named_tuple_with_score_velocity_is_trending(self):
        post = {
            "reactions_total": 200,
            "post_timestamp": _now() - timedelta(hours=2),
        }
        result = score_trending(post)
        assert isinstance(result, TrendingScore)
        assert hasattr(result, "score")
        assert hasattr(result, "velocity")
        assert hasattr(result, "is_trending")


class TestVelocityFormula:
    def test_velocity_is_reactions_per_hour(self):
        post = {
            "reactions_total": 120,
            "post_timestamp": _now() - timedelta(hours=2),
        }
        result = score_trending(post)
        assert result.velocity == pytest.approx(60.0, rel=0.05)

    def test_very_fresh_post_caps_age_at_30_min(self):
        """Posts <30m old cap age=0.5h so velocity doesn't explode."""
        post = {
            "reactions_total": 30,
            "post_timestamp": _now() - timedelta(minutes=5),
        }
        result = score_trending(post)
        # 30 / 0.5 = 60
        assert result.velocity == pytest.approx(60.0, rel=0.05)

    def test_missing_timestamp_uses_fallback_1h(self):
        post = {"reactions_total": 100, "post_timestamp": None}
        result = score_trending(post)
        assert result.velocity == pytest.approx(100.0, rel=0.05)

    def test_zero_reactions_zero_velocity(self):
        post = {
            "reactions_total": 0,
            "post_timestamp": _now() - timedelta(hours=1),
        }
        result = score_trending(post)
        assert result.velocity == 0.0


class TestScoreFormula:
    def test_score_combines_velocity_and_reactions(self):
        """score = 0.7 * velocity + 0.3 * reactions_total"""
        post = {
            "reactions_total": 100,
            "post_timestamp": _now() - timedelta(hours=1),
        }
        result = score_trending(post)
        # velocity=100, reactions=100 -> 0.7*100 + 0.3*100 = 100
        assert result.score == pytest.approx(100.0, rel=0.01)

    def test_score_rounded_to_two_decimals(self):
        post = {
            "reactions_total": 33,
            "post_timestamp": _now() - timedelta(hours=1),
        }
        result = score_trending(post)
        # velocity=33, reactions=33 -> 0.7*33 + 0.3*33 = 33
        assert result.score == pytest.approx(33.0, abs=0.01)


class TestTrendingThreshold:
    def test_meets_velocity_threshold_50_is_trending(self):
        post = {
            "reactions_total": 100,
            "post_timestamp": _now() - timedelta(hours=1),
        }
        result = score_trending(post)
        assert result.is_trending is True

    def test_meets_absolute_threshold_100_is_trending(self):
        """Even slow-burn post passes if absolute reactions >= 100."""
        post = {
            "reactions_total": 120,
            "post_timestamp": _now() - timedelta(hours=20),
        }
        result = score_trending(post)
        # velocity=6 (low), but reactions>=100 -> still trending
        assert result.is_trending is True

    def test_below_both_thresholds_not_trending(self):
        post = {
            "reactions_total": 20,
            "post_timestamp": _now() - timedelta(hours=2),
        }
        result = score_trending(post)
        # velocity=10, reactions=20 -> both below
        assert result.is_trending is False

    def test_stale_post_older_than_24h_not_trending(self):
        """Freshness filter: ignore anything >24h old regardless of score."""
        post = {
            "reactions_total": 500,
            "post_timestamp": _now() - timedelta(hours=30),
        }
        result = score_trending(post)
        assert result.is_trending is False

    def test_zero_reactions_not_trending(self):
        post = {
            "reactions_total": 0,
            "post_timestamp": _now() - timedelta(hours=1),
        }
        result = score_trending(post)
        assert result.is_trending is False


class TestInputHandling:
    def test_accepts_iso_string_timestamp(self):
        ts = (_now() - timedelta(hours=1)).isoformat()
        post = {"reactions_total": 100, "post_timestamp": ts}
        result = score_trending(post)
        assert result.velocity == pytest.approx(100.0, rel=0.1)

    def test_accepts_iso_z_timestamp(self):
        ts = (
            _now()
            .replace(tzinfo=None)
            .replace(microsecond=0)
            .isoformat()
            + "Z"
        )
        # 1 hour in the past
        from datetime import datetime as dt

        past = dt.utcnow() - timedelta(hours=1)
        ts = past.replace(microsecond=0).isoformat() + "Z"
        post = {"reactions_total": 100, "post_timestamp": ts}
        result = score_trending(post)
        assert result.velocity > 0

    def test_unparseable_timestamp_falls_back(self):
        post = {"reactions_total": 100, "post_timestamp": "not-a-date"}
        result = score_trending(post)
        assert result.velocity == pytest.approx(100.0, rel=0.05)

    def test_naive_datetime_assumed_utc(self):
        naive = datetime.utcnow() - timedelta(hours=1)
        post = {"reactions_total": 100, "post_timestamp": naive}
        result = score_trending(post)
        assert result.velocity == pytest.approx(100.0, rel=0.1)

    def test_missing_reactions_defaults_zero(self):
        post = {"post_timestamp": _now() - timedelta(hours=1)}
        result = score_trending(post)
        assert result.velocity == 0.0
        assert result.is_trending is False
