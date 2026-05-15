"""Tests for Celery beat schedule knobs.

Phase I-D (deprecated) — fixed scan interval.
Phase J — randomized cadence + self-rescheduling task chain. Beat no longer
drives scan_all_sources directly; instead it runs ``scan_watchdog`` every 5
minutes as a safety net for stale chains. Each finished scan picks its own
``random.uniform(SCAN_MIN_INTERVAL, SCAN_MAX_INTERVAL)`` countdown for the
next run via ``apply_async``.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def reload_celery_app(monkeypatch):
    """Reload ``bot.celery_app`` fresh so env-var reads re-run."""

    def _reload():
        import bot.celery_app as mod

        return importlib.reload(mod)

    return _reload


class TestScanIntervalLegacy:
    """Phase I-D legacy ``_scan_interval()`` is kept for backwards compat
    (no caller in normal beat flow), but env override + default still work.
    """

    def test_default_is_30_minutes(self, reload_celery_app, monkeypatch):
        monkeypatch.delenv("SCAN_INTERVAL_SECONDS", raising=False)
        mod = reload_celery_app()
        assert mod._scan_interval() == 1800

    def test_env_override_respected(self, reload_celery_app, monkeypatch):
        monkeypatch.setenv("SCAN_INTERVAL_SECONDS", "2400")
        mod = reload_celery_app()
        assert mod._scan_interval() == 2400


class TestScanRandomizedRange:
    """Phase J-1 — random cadence bounds.

    Range default 600..1500s (10..25 min). Self-rescheduler picks a
    ``random.uniform`` countdown within this range each cycle so FB
    anti-bot can't track a fixed inter-scan delta.
    """

    def test_min_default_is_10_minutes(self, reload_celery_app, monkeypatch):
        monkeypatch.delenv("SCAN_MIN_INTERVAL_SECONDS", raising=False)
        mod = reload_celery_app()
        assert mod._scan_min_interval() == 600

    def test_max_default_is_25_minutes(self, reload_celery_app, monkeypatch):
        monkeypatch.delenv("SCAN_MAX_INTERVAL_SECONDS", raising=False)
        mod = reload_celery_app()
        assert mod._scan_max_interval() == 1500

    def test_min_env_override(self, reload_celery_app, monkeypatch):
        monkeypatch.setenv("SCAN_MIN_INTERVAL_SECONDS", "900")
        mod = reload_celery_app()
        assert mod._scan_min_interval() == 900

    def test_max_env_override(self, reload_celery_app, monkeypatch):
        monkeypatch.setenv("SCAN_MAX_INTERVAL_SECONDS", "1200")
        mod = reload_celery_app()
        assert mod._scan_max_interval() == 1200

    def test_min_strictly_less_than_max_at_default(
        self, reload_celery_app, monkeypatch
    ):
        """Sanity invariant — defaults must satisfy ``min < max``."""
        monkeypatch.delenv("SCAN_MIN_INTERVAL_SECONDS", raising=False)
        monkeypatch.delenv("SCAN_MAX_INTERVAL_SECONDS", raising=False)
        mod = reload_celery_app()
        assert mod._scan_min_interval() < mod._scan_max_interval()


class TestScanWatchdogInterval:
    """Phase J-1 — watchdog beat schedule.

    Watchdog runs every 5 min by default, queries last ScannerRun, kicks
    a fresh ``scan_all_sources`` if the chain has gone idle past
    ``SCAN_MAX_IDLE_SECONDS``. Cheap DB query, runs forever.
    """

    def test_default_is_5_minutes(self, reload_celery_app, monkeypatch):
        monkeypatch.delenv("SCAN_WATCHDOG_INTERVAL_SECONDS", raising=False)
        mod = reload_celery_app()
        assert mod._scan_watchdog_interval() == 300

    def test_env_override_respected(self, reload_celery_app, monkeypatch):
        monkeypatch.setenv("SCAN_WATCHDOG_INTERVAL_SECONDS", "180")
        mod = reload_celery_app()
        assert mod._scan_watchdog_interval() == 180


class TestBeatSchedule:
    """Phase J-1 — beat schedule shape.

    ``scan-all-sources`` entry REMOVED (self-rescheduling takes over).
    ``scan-watchdog`` entry ADDED with the watchdog interval.
    Other entries (collect-all-targets, daily-summary, weekly-report,
    health-check) untouched.
    """

    def test_scan_all_sources_removed_from_beat(
        self, reload_celery_app, monkeypatch
    ):
        mod = reload_celery_app()
        assert "scan-all-sources" not in mod.app.conf.beat_schedule

    def test_scan_watchdog_present_in_beat(self, reload_celery_app, monkeypatch):
        mod = reload_celery_app()
        assert "scan-watchdog" in mod.app.conf.beat_schedule
        entry = mod.app.conf.beat_schedule["scan-watchdog"]
        assert entry["task"] == "bot.tasks.scan_watchdog"
        assert entry["schedule"] == 300  # default 5 min

    def test_scan_watchdog_schedule_respects_env(
        self, reload_celery_app, monkeypatch
    ):
        monkeypatch.setenv("SCAN_WATCHDOG_INTERVAL_SECONDS", "120")
        mod = reload_celery_app()
        assert mod.app.conf.beat_schedule["scan-watchdog"]["schedule"] == 120

    def test_other_beat_entries_preserved(self, reload_celery_app, monkeypatch):
        mod = reload_celery_app()
        for key in (
            "collect-all-targets",
            "health-check",
            "daily-summary",
            "weekly-report",
        ):
            assert key in mod.app.conf.beat_schedule, (
                f"beat schedule lost preserved entry: {key}"
            )
