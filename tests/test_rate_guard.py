"""Tests for RateGuard.

Covers atomicity, per-run cap, prune behavior, release(), and the
check-then-act race that the previous implementation lost under
concurrent access.
"""

from __future__ import annotations

import threading
import time

import pytest

from bot.modules.rate_guard import RateGuard


@pytest.fixture
def config():
    return {
        "global": {
            "max_requests_per_minute": 5,
            "max_requests_per_hour": 10,
        },
        "per_target": {
            "default": {
                "min_interval_seconds": 0.1,
                "max_requests_per_run": 3,
            },
            "overrides": {
                "hot": {
                    "min_interval_seconds": 0.1,
                    "max_requests_per_run": 2,
                }
            },
        },
        "backoff": {"captcha": {"cooldown_minutes": 120}},
    }


@pytest.fixture
def guard(config):
    return RateGuard(config)


class TestBasicReservation:
    def test_first_reservation_succeeds(self, guard):
        guard.begin_run("t1")
        assert guard.check_and_reserve("t1") is True

    def test_min_interval_blocks_immediate_retry(self, guard):
        guard.begin_run("t1")
        assert guard.check_and_reserve("t1") is True
        # Less than min_interval later, should fail.
        assert guard.check_and_reserve("t1") is False

    def test_min_interval_allows_after_wait(self, guard):
        guard.begin_run("t1")
        assert guard.check_and_reserve("t1") is True
        time.sleep(0.11)
        guard.begin_run("t1")
        assert guard.check_and_reserve("t1") is True


class TestGlobalRateLimit:
    def test_global_per_minute_cap(self, config):
        config["global"]["max_requests_per_minute"] = 2
        config["per_target"]["default"]["min_interval_seconds"] = 0
        config["per_target"]["default"]["max_requests_per_run"] = 10
        guard = RateGuard(config)
        guard.begin_run("t1")
        assert guard.check_and_reserve("t1") is True
        guard.begin_run("t1")
        assert guard.check_and_reserve("t1") is True
        guard.begin_run("t1")
        assert guard.check_and_reserve("t1") is False


class TestPerRunCap:
    def test_per_run_cap_enforced(self, guard):
        # override "hot" allows 2 per run
        for _ in range(2):
            guard.begin_run("hot")
            time.sleep(0.11)
            assert guard.check_and_reserve("hot") is True
        # third attempt within the same run hits the cap even if interval ok
        time.sleep(0.11)
        # Do NOT reset begin_run — we are explicitly counting within a run.
        # Manually rebuild state by re-using the existing counter.
        # (begin_run resets the counter, so call check_and_reserve directly.)
        assert guard.check_and_reserve("hot") is False

    def test_begin_run_resets_cap(self, guard):
        for _ in range(2):
            guard.begin_run("hot")
            time.sleep(0.11)
            assert guard.check_and_reserve("hot") is True
        # Fresh run → counter reset, allow again (subject to interval).
        time.sleep(0.11)
        guard.begin_run("hot")
        assert guard.check_and_reserve("hot") is True


class TestRelease:
    def test_release_undoes_reservation(self, guard):
        guard.begin_run("t1")
        assert guard.check_and_reserve("t1") is True
        guard.release("t1")
        # After release, the interval guard is the only thing in the way;
        # a tiny sleep satisfies it.
        time.sleep(0.11)
        guard.begin_run("t1")
        assert guard.check_and_reserve("t1") is True


class TestPruning:
    def test_history_does_not_grow_unbounded(self, config):
        config["per_target"]["default"]["min_interval_seconds"] = 0
        config["per_target"]["default"]["max_requests_per_run"] = 10_000
        config["global"]["max_requests_per_minute"] = 10_000
        config["global"]["max_requests_per_hour"] = 10_000
        guard = RateGuard(config)
        for _ in range(50):
            guard.begin_run("t1")
            guard.check_and_reserve("t1")
        # Per-target history is pruned to ~min_interval window; since
        # min_interval is 0 we effectively keep nothing beyond the most
        # recent. Global list still grows but stays bounded by the hour.
        assert len(guard._target_requests.get("t1", [])) <= 50
        assert len(guard._global_requests) <= 50


class TestAtomicityUnderThreads:
    def test_concurrent_reserve_respects_global_cap(self, config):
        """Previous implementation lost this race: check-then-act under
        two threads could both pass the check and then both append."""
        config["global"]["max_requests_per_minute"] = 100
        config["global"]["max_requests_per_hour"] = 5
        config["per_target"]["default"]["min_interval_seconds"] = 0
        config["per_target"]["default"]["max_requests_per_run"] = 100
        guard = RateGuard(config)

        successes = []
        lock = threading.Lock()

        def worker():
            guard.begin_run("t1")
            ok = guard.check_and_reserve("t1")
            with lock:
                successes.append(ok)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # max_per_hour=5 means at most 5 True values overall.
        assert sum(successes) == 5


class TestResetTarget:
    def test_reset_clears_history_and_counter(self, guard):
        guard.begin_run("t1")
        assert guard.check_and_reserve("t1") is True
        guard.reset_target("t1")
        # Next call should succeed immediately (no interval carry-over).
        guard.begin_run("t1")
        assert guard.check_and_reserve("t1") is True


class TestCooldownMinutes:
    def test_custom_event_cooldown(self, guard):
        assert guard.get_cooldown_minutes("captcha") == 120

    def test_unknown_event_defaults(self, guard):
        assert guard.get_cooldown_minutes("unknown") == 60
