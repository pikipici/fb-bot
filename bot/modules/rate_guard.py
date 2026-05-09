"""Rate Guard — throttling for global and per-target requests.

Thread-safety contract:

* All public mutators acquire a single module-level ``RLock`` so
  ``check_and_reserve`` is genuinely atomic. Two concurrent callers can
  no longer both pass the check and then both append a reservation.
* Request timestamp lists are pruned on every evaluation so the data
  structure stays O(max_per_hour) per key instead of growing unbounded
  in long-lived Celery workers.
* ``max_requests_per_run`` is enforced (was dead config before).
"""

from __future__ import annotations

import threading
import time
from typing import Any


class RateGuard:
    """Manage rate limiting for collector requests."""

    def __init__(self, config: dict[str, Any]):
        self.global_config = config.get("global", {})
        self.per_target_config = config.get("per_target", {})
        self.backoff_config = config.get("backoff", {})

        # Re-entrant so nested calls (e.g. ``check_and_reserve`` calling
        # the private helpers) don't deadlock.
        self._lock = threading.RLock()

        self._global_requests: list[float] = []
        self._target_requests: dict[str, list[float]] = {}
        # Per-target counter that resets each time ``begin_run`` is called.
        # Used to enforce the per-run cap independently of wall-clock.
        self._target_run_counts: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def begin_run(self, target_id: str) -> None:
        """Reset the per-run counter for ``target_id``.

        Call once at the start of a collector iteration before any
        ``check_and_reserve`` call. Idempotent.
        """
        with self._lock:
            self._target_run_counts[target_id] = 0

    def can_reserve(self, target_id: str) -> bool:
        """Non-mutating peek at whether a reservation would succeed.

        Use this from a scheduler to *filter* eligible targets without
        consuming quota. The actual reservation still happens via
        ``check_and_reserve`` at the point of use — otherwise targets
        that never fetch would silently burn through the rate budget.
        """
        now = time.time()
        with self._lock:
            return self._check_global(now) and self._check_target(target_id, now)

    def check_and_reserve(self, target_id: str) -> bool:
        """Atomically check all rate limits and reserve a slot.

        Returns ``True`` when the caller is allowed to make a request.
        Returns ``False`` and leaves state unchanged when any limit is
        exhausted.
        """
        now = time.time()
        with self._lock:
            if not self._check_global(now):
                return False
            if not self._check_target(target_id, now):
                return False

            self._global_requests.append(now)
            self._target_requests.setdefault(target_id, []).append(now)
            self._target_run_counts[target_id] = (
                self._target_run_counts.get(target_id, 0) + 1
            )
            return True

    def release(self, target_id: str) -> None:
        """Undo the most recent reservation for ``target_id``.

        Call this from the caller's error path when the guarded
        operation did not actually run — otherwise a failed attempt
        consumes quota anyway and can starve legitimate calls.
        """
        with self._lock:
            if self._global_requests:
                self._global_requests.pop()
            target_reqs = self._target_requests.get(target_id)
            if target_reqs:
                target_reqs.pop()
                if not target_reqs:
                    self._target_requests.pop(target_id, None)
            count = self._target_run_counts.get(target_id, 0)
            if count > 0:
                self._target_run_counts[target_id] = count - 1

    def get_cooldown_minutes(self, event_type: str) -> int:
        """Return the backoff cooldown (minutes) for an event type."""
        event_config = self.backoff_config.get(event_type, {})
        return int(event_config.get("cooldown_minutes", 60))

    def reset_target(self, target_id: str) -> None:
        """Reset rate limit state for a target (history + run counter)."""
        with self._lock:
            self._target_requests.pop(target_id, None)
            self._target_run_counts.pop(target_id, None)

    # ------------------------------------------------------------------
    # Internal helpers (must be called under ``self._lock``)
    # ------------------------------------------------------------------
    def _target_config_for(self, target_id: str) -> dict[str, Any]:
        overrides = self.per_target_config.get("overrides", {})
        return overrides.get(target_id, self.per_target_config.get("default", {}))

    def _check_global(self, now: float) -> bool:
        max_per_minute = int(self.global_config.get("max_requests_per_minute", 60))
        max_per_hour = int(self.global_config.get("max_requests_per_hour", 1000))

        one_minute_ago = now - 60
        one_hour_ago = now - 3600
        # Prune: drop anything older than the widest window we care about.
        self._global_requests = [
            t for t in self._global_requests if t > one_hour_ago
        ]

        recent_minute = sum(1 for t in self._global_requests if t > one_minute_ago)
        if recent_minute >= max_per_minute:
            return False
        if len(self._global_requests) >= max_per_hour:
            return False
        return True

    def _check_target(self, target_id: str, now: float) -> bool:
        cfg = self._target_config_for(target_id)
        min_interval = float(cfg.get("min_interval_seconds", 30))
        max_per_run = cfg.get("max_requests_per_run")

        # Prune per-target history to the min_interval window so the list
        # cannot grow beyond a few entries for an idle target.
        cutoff = now - max(min_interval, 60.0)
        history = self._target_requests.get(target_id)
        if history is not None:
            history = [t for t in history if t > cutoff]
            if history:
                self._target_requests[target_id] = history
            else:
                self._target_requests.pop(target_id, None)

        history = self._target_requests.get(target_id, [])
        if history and (now - history[-1]) < min_interval:
            return False

        if max_per_run is not None:
            current = self._target_run_counts.get(target_id, 0)
            if current >= int(max_per_run):
                return False

        return True
