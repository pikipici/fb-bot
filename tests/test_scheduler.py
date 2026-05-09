"""Tests for TargetScheduler module."""

import time

import pytest

from bot.modules.circuit_breaker import CircuitBreaker
from bot.modules.rate_guard import RateGuard
from bot.modules.scheduler import TargetScheduler


@pytest.fixture
def sample_targets():
    return [
        {
            "id": "target_high",
            "name": "High Priority",
            "type": "group",
            "url": "https://facebook.com/groups/high",
            "mode": "scrape_public",
            "priority": 20,
            "cooldown_minutes": 30,
            "max_posts_per_run": 50,
            "enabled": True,
        },
        {
            "id": "target_low",
            "name": "Low Priority",
            "type": "group",
            "url": "https://facebook.com/groups/low",
            "mode": "scrape_public",
            "priority": 5,
            "cooldown_minutes": 60,
            "max_posts_per_run": 30,
            "enabled": True,
        },
        {
            "id": "target_disabled",
            "name": "Disabled Target",
            "type": "page",
            "url": "https://facebook.com/disabled",
            "mode": "api_first",
            "priority": 15,
            "cooldown_minutes": 30,
            "max_posts_per_run": 50,
            "enabled": False,
        },
    ]


@pytest.fixture
def scheduler(sample_targets):
    return TargetScheduler(targets=sample_targets)


class TestGetRunnableTargets:
    def test_returns_enabled_targets_sorted_by_priority(self, scheduler):
        runnable = scheduler.get_runnable_targets()
        assert len(runnable) == 2
        assert runnable[0]["id"] == "target_high"
        assert runnable[1]["id"] == "target_low"

    def test_excludes_disabled_targets(self, scheduler):
        runnable = scheduler.get_runnable_targets()
        ids = [t["id"] for t in runnable]
        assert "target_disabled" not in ids

    def test_excludes_suspended_targets(self, sample_targets):
        cb = CircuitBreaker(failure_threshold=1, degraded_threshold=2)
        # Suspend target_high
        cb.record_failure("target_high")
        cb.record_failure("target_high")

        scheduler = TargetScheduler(targets=sample_targets, circuit_breaker=cb)
        runnable = scheduler.get_runnable_targets()
        ids = [t["id"] for t in runnable]
        assert "target_high" not in ids
        assert "target_low" in ids

    def test_excludes_targets_in_cooldown(self, scheduler):
        # Mark target_high as just run
        scheduler.mark_run("target_high")

        runnable = scheduler.get_runnable_targets()
        ids = [t["id"] for t in runnable]
        assert "target_high" not in ids
        assert "target_low" in ids

    def test_includes_target_after_cooldown_expires(self, scheduler):
        # Mark as run 31 minutes ago (cooldown is 30min)
        scheduler.mark_run_at("target_high", time.time() - 31 * 60)

        runnable = scheduler.get_runnable_targets()
        ids = [t["id"] for t in runnable]
        assert "target_high" in ids

    def test_empty_targets(self):
        scheduler = TargetScheduler(targets=[])
        assert scheduler.get_runnable_targets() == []

    def test_all_disabled(self):
        targets = [{"id": "t1", "enabled": False}, {"id": "t2", "enabled": False}]
        scheduler = TargetScheduler(targets=targets)
        assert scheduler.get_runnable_targets() == []


class TestMarkRun:
    def test_mark_run_sets_timestamp(self, scheduler):
        before = time.time()
        scheduler.mark_run("target_high")
        after = time.time()
        assert before <= scheduler._last_run["target_high"] <= after

    def test_mark_run_at_specific_time(self, scheduler):
        ts = 1000000.0
        scheduler.mark_run_at("target_low", ts)
        assert scheduler._last_run["target_low"] == ts


class TestGetTargetById:
    def test_found(self, scheduler):
        target = scheduler.get_target_by_id("target_high")
        assert target is not None
        assert target["name"] == "High Priority"

    def test_not_found(self, scheduler):
        assert scheduler.get_target_by_id("nonexistent") is None


class TestLoadFromFile:
    def test_load_from_valid_path(self, tmp_path):
        config = tmp_path / "targets.json"
        config.write_text('{"targets": [{"id": "t1", "enabled": true, "priority": 1}]}')

        scheduler = TargetScheduler(config_path=config)
        assert len(scheduler.all_targets) == 1
        assert scheduler.all_targets[0]["id"] == "t1"

    def test_load_from_missing_path(self, tmp_path):
        scheduler = TargetScheduler(config_path=tmp_path / "nope.json")
        assert scheduler.all_targets == []

    def test_load_from_invalid_json(self, tmp_path):
        config = tmp_path / "bad.json"
        config.write_text("not json at all")

        scheduler = TargetScheduler(config_path=config)
        assert scheduler.all_targets == []

    def test_reload_targets(self, tmp_path):
        config = tmp_path / "targets.json"
        config.write_text('{"targets": [{"id": "t1", "enabled": true}]}')

        scheduler = TargetScheduler(config_path=config)
        assert len(scheduler.all_targets) == 1

        # Update file
        config.write_text('{"targets": [{"id": "t1"}, {"id": "t2"}]}')
        scheduler.reload_targets(config)
        assert len(scheduler.all_targets) == 2


class TestAllTargetsProperty:
    def test_returns_copy(self, scheduler, sample_targets):
        targets = scheduler.all_targets
        targets.append({"id": "injected"})
        # Original should not be modified
        assert len(scheduler.all_targets) == len(sample_targets)
