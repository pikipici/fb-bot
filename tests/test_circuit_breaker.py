"""Tests for ``CircuitBreaker``.

Covers:
* Failure counting within the rolling window.
* Promote-to-SUSPENDED then natural expiry back to DEGRADED (the bug
  where ``get_status`` kept reporting SUSPENDED from storage forever).
* record_success clears all state for the target.
"""

from __future__ import annotations

import time

from bot.modules.circuit_breaker import CircuitBreaker, TargetStatus


def _cb(**overrides) -> CircuitBreaker:
    return CircuitBreaker(
        failure_threshold=overrides.get("failure_threshold", 3),
        degraded_threshold=overrides.get("degraded_threshold", 5),
        cooldown_seconds=overrides.get("cooldown_seconds", 1800),
        window_seconds=overrides.get("window_seconds", 3600),
    )


class TestFailureTracking:
    def test_below_threshold_stays_active(self):
        cb = _cb()
        for _ in range(2):
            status = cb.record_failure("t1")
        assert status == TargetStatus.ACTIVE

    def test_degraded_after_failure_threshold(self):
        cb = _cb(failure_threshold=3, degraded_threshold=5)
        for _ in range(3):
            status = cb.record_failure("t1")
        assert status == TargetStatus.DEGRADED

    def test_suspended_after_degraded_threshold(self):
        cb = _cb(failure_threshold=3, degraded_threshold=5)
        for _ in range(5):
            status = cb.record_failure("t1")
        assert status == TargetStatus.SUSPENDED


class TestCooldownExpiry:
    def test_status_clears_from_suspended_after_cooldown(self, monkeypatch):
        cb = _cb(failure_threshold=1, degraded_threshold=2, cooldown_seconds=10)
        cb.record_failure("t1")
        cb.record_failure("t1")
        assert cb.get_status("t1") == TargetStatus.SUSPENDED

        # Move "now" past the cooldown window.
        suspended_at = cb._suspended_at["t1"]
        fake_now = suspended_at + 11

        def _fake_time():
            return fake_now

        monkeypatch.setattr("bot.modules.circuit_breaker.time.time", _fake_time)

        # After the promotion, state is consistent across both APIs.
        assert cb.get_status("t1") == TargetStatus.DEGRADED
        assert cb._status["t1"] == TargetStatus.DEGRADED
        assert "t1" not in cb._suspended_at
        assert cb.get_all_statuses()["t1"] == TargetStatus.DEGRADED


class TestRecordSuccess:
    def test_success_clears_all_state(self):
        cb = _cb(failure_threshold=1, degraded_threshold=2)
        cb.record_failure("t1")
        cb.record_failure("t1")
        cb.record_success("t1")
        assert cb.get_status("t1") == TargetStatus.ACTIVE
        assert cb.is_available("t1") is True
        assert "t1" not in cb._failures
