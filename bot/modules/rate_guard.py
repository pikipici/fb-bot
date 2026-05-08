"""Rate Guard — throttling for global and per-target requests."""

import time
from typing import Any


class RateGuard:
    """Manage rate limiting for collector requests."""

    def __init__(self, config: dict[str, Any]):
        self.global_config = config.get("global", {})
        self.per_target_config = config.get("per_target", {})
        self.backoff_config = config.get("backoff", {})

        self._global_requests: list[float] = []
        self._target_requests: dict[str, list[float]] = {}

    def check_and_reserve(self, target_id: str) -> bool:
        """Check if a request can be made. Returns True if allowed."""
        now = time.time()

        # Check global rate limit
        if not self._check_global(now):
            return False

        # Check per-target rate limit
        if not self._check_target(target_id, now):
            return False

        # Reserve slot
        self._global_requests.append(now)
        if target_id not in self._target_requests:
            self._target_requests[target_id] = []
        self._target_requests[target_id].append(now)

        return True

    def _check_global(self, now: float) -> bool:
        """Check global rate limits."""
        max_per_minute = self.global_config.get("max_requests_per_minute", 60)
        max_per_hour = self.global_config.get("max_requests_per_hour", 1000)

        # Clean old entries
        one_minute_ago = now - 60
        one_hour_ago = now - 3600
        self._global_requests = [t for t in self._global_requests if t > one_hour_ago]

        # Check per-minute
        recent = [t for t in self._global_requests if t > one_minute_ago]
        if len(recent) >= max_per_minute:
            return False

        # Check per-hour
        if len(self._global_requests) >= max_per_hour:
            return False

        return True

    def _check_target(self, target_id: str, now: float) -> bool:
        """Check per-target rate limits."""
        overrides = self.per_target_config.get("overrides", {})
        if target_id in overrides:
            target_config = overrides[target_id]
        else:
            target_config = self.per_target_config.get("default", {})

        min_interval = target_config.get("min_interval_seconds", 30)

        if target_id in self._target_requests:
            last_request = self._target_requests[target_id]
            if last_request and (now - last_request[-1]) < min_interval:
                return False

        return True

    def get_cooldown_minutes(self, event_type: str) -> int:
        """Get cooldown duration for a specific event type."""
        event_config = self.backoff_config.get(event_type, {})
        return event_config.get("cooldown_minutes", 60)

    def reset_target(self, target_id: str):
        """Reset rate limit state for a target."""
        self._target_requests.pop(target_id, None)
