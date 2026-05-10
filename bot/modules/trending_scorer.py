"""Trending scorer — velocity + absolute-reactions heuristic.

Used by the Layer-1 scanner to decide whether a collected post should
be persisted to ``trending_posts`` and surfaced on the ``/trending``
page. Deliberately separate from ``scorer.ScoringEngine`` which scores
for the older review-queue pipeline.

Formula::

    age_hours = (now_utc - post_timestamp_utc).total_seconds() / 3600
    age_hours = max(age_hours, 0.5)   # cap so very-fresh posts don't
                                      # produce infinite velocity
    velocity  = reactions_total / age_hours
    score     = 0.7 * velocity + 0.3 * reactions_total

    is_trending = (
        age_hours < 24
        and reactions_total > 0
        and (velocity >= 50 or reactions_total >= 100)
    )

Design notes:
- We score with reactions_total rather than (likes+comments+shares) so
  the caller can decide how to aggregate. In practice the collector
  fills ``reactions_total = likes + comments + shares`` plus any
  reaction counts FB exposes.
- ``post_timestamp`` accepts ``datetime``, ISO-8601 string, ``"Z"``
  suffix, naive (assumed UTC), or ``None``. Unparseable input falls
  back to a 1-hour age so a newly collected post without a parsed
  timestamp still surfaces if engagement is there.
- Score is rounded to 2 decimals for display, but ``is_trending`` is
  decided on the raw value.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final

_MIN_AGE_HOURS: Final = 0.5
_FALLBACK_AGE_HOURS: Final = 1.0
_MAX_AGE_HOURS: Final = 24.0
_VELOCITY_THRESHOLD: Final = 50.0
_ABSOLUTE_THRESHOLD: Final = 100
_VELOCITY_WEIGHT: Final = 0.7
_ABSOLUTE_WEIGHT: Final = 0.3


@dataclass(frozen=True)
class TrendingScore:
    score: float
    velocity: float
    is_trending: bool


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        raw = value.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _age_hours(post_timestamp: Any, *, now: datetime) -> float:
    dt = _coerce_datetime(post_timestamp)
    if dt is None:
        return _FALLBACK_AGE_HOURS
    age = (now - dt).total_seconds() / 3600.0
    if age < _MIN_AGE_HOURS:
        return _MIN_AGE_HOURS
    return age


def score_trending(post: dict[str, Any], *, now: datetime | None = None) -> TrendingScore:
    """Compute the trending score for a collected post.

    Args:
        post: dict that must expose ``reactions_total`` (int) and
            ``post_timestamp`` (datetime | ISO string | None).
        now: optional override (helpful in tests). Defaults to
            ``datetime.now(timezone.utc)``.
    """
    reference = now or datetime.now(timezone.utc)
    reactions = int(post.get("reactions_total") or 0)
    age = _age_hours(post.get("post_timestamp"), now=reference)

    velocity = reactions / age if age > 0 else 0.0
    score = _VELOCITY_WEIGHT * velocity + _ABSOLUTE_WEIGHT * reactions

    is_trending = (
        age < _MAX_AGE_HOURS
        and reactions > 0
        and (
            velocity >= _VELOCITY_THRESHOLD
            or reactions >= _ABSOLUTE_THRESHOLD
        )
    )

    return TrendingScore(
        score=round(score, 2),
        velocity=round(velocity, 2),
        is_trending=is_trending,
    )
