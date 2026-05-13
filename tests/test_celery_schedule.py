"""Tests for Celery beat schedule knobs (Phase I-D scanner cadence)."""
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


class TestScanInterval:
    """Phase I-D-1 — default scan interval bumped from 15min to 30min.

    Rationale: FB anti-bot flags rapid auth rhythm; 15-min cadence from
    a single VPS IP is a strong bot tell. Bumping default to 30min while
    still allowing env override keeps ops flexibility.
    """

    def test_default_is_30_minutes(self, reload_celery_app, monkeypatch):
        monkeypatch.delenv("SCAN_INTERVAL_SECONDS", raising=False)
        mod = reload_celery_app()
        assert mod._scan_interval() == 1800

    def test_env_override_respected(self, reload_celery_app, monkeypatch):
        monkeypatch.setenv("SCAN_INTERVAL_SECONDS", "2400")
        mod = reload_celery_app()
        assert mod._scan_interval() == 2400

    def test_default_is_at_least_25_minutes(
        self, reload_celery_app, monkeypatch
    ):
        """Guardrail: don't accidentally regress below the human-like
        threshold when tweaking. 1500s == 25min lower bound per plan §3.D-1.
        """
        monkeypatch.delenv("SCAN_INTERVAL_SECONDS", raising=False)
        mod = reload_celery_app()
        assert mod._scan_interval() >= 1500
