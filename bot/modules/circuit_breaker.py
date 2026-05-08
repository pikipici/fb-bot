"""Circuit Breaker — manages target health status."""

import time
from enum import Enum
from typing import Any


class TargetStatus(str, Enum):
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    SUSPENDED = "SUSPENDED"


class CircuitBreaker:
    """Track target health and manage circuit breaking."""

    def __init__(
        self,
        failure_threshold: int = 3,
        degraded_threshold: int = 5,
        cooldown_seconds: int = 1800,
        window_seconds: int = 3600,
    ):
        self.failure_threshold = failure_threshold
        self.degraded_threshold = degraded_threshold
        self.cooldown_seconds = cooldown_seconds
        self.window_seconds = window_seconds

        self._failures: dict[str, list[float]] = {}
        self._status: dict[str, TargetStatus] = {}
        self._suspended_at: dict[str, float] = {}

    def record_failure(self, target_id: str) -> TargetStatus:
        """Record a failure for a target and return updated status."""
        now = time.time()

        if target_id not in self._failures:
            self._failures[target_id] = []

        self._failures[target_id].append(now)

        # Clean old failures outside window
        cutoff = now - self.window_seconds
        self._failures[target_id] = [
            t for t in self._failures[target_id] if t > cutoff
        ]

        failure_count = len(self._failures[target_id])

        if failure_count >= self.degraded_threshold:
            self._status[target_id] = TargetStatus.SUSPENDED
            self._suspended_at[target_id] = now
        elif failure_count >= self.failure_threshold:
            self._status[target_id] = TargetStatus.DEGRADED
        else:
            self._status[target_id] = TargetStatus.ACTIVE

        return self._status[target_id]

    def record_success(self, target_id: str):
        """Record a success, potentially recovering the target."""
        self._status[target_id] = TargetStatus.ACTIVE
        self._failures.pop(target_id, None)
        self._suspended_at.pop(target_id, None)

    def get_status(self, target_id: str) -> TargetStatus:
        """Get current status of a target."""
        if target_id in self._suspended_at:
            elapsed = time.time() - self._suspended_at[target_id]
            if elapsed >= self.cooldown_seconds:
                # Cooldown expired, allow health probe
                return TargetStatus.DEGRADED
        return self._status.get(target_id, TargetStatus.ACTIVE)

    def is_available(self, target_id: str) -> bool:
        """Check if target is available for collection."""
        status = self.get_status(target_id)
        return status != TargetStatus.SUSPENDED

    def get_all_statuses(self) -> dict[str, TargetStatus]:
        """Get status of all tracked targets."""
        return {tid: self.get_status(tid) for tid in self._status}
