"""Phase M-1 — Celery hard time_limit + soft_time_limit defense.

Layer baru di atas Phase L-2 watchdog: kalau Playwright pipe deadlock
nyangkutin worker process, watchdog cuma fix DB row tapi worker tetap
hang. ``time_limit`` paksa Celery SIGKILL worker setelah N detik →
systemd auto-restart → cgroup cleanup termasuk Chromium subprocess.
``soft_time_limit`` raise ``SoftTimeLimitExceeded`` lebih dulu biar
task bisa cleanup graceful (finalize ScannerRun, reschedule).

Defaults:
  scan_all_sources       time_limit=900, soft=720   (15min hard / 12min soft)
  auto_comment_next      time_limit=600, soft=480   (10min hard / 8min soft)
"""
from __future__ import annotations


class TestScanAllSourcesTimeLimits:
    """``scan_all_sources`` decorator carries hard + soft time limits."""

    def test_scan_all_sources_has_time_limit_900(self):
        from bot.tasks import scan_all_sources

        assert scan_all_sources.time_limit == 900

    def test_scan_all_sources_has_soft_time_limit_720(self):
        from bot.tasks import scan_all_sources

        assert scan_all_sources.soft_time_limit == 720

    def test_scan_soft_strictly_less_than_hard(self):
        from bot.tasks import scan_all_sources

        assert scan_all_sources.soft_time_limit < scan_all_sources.time_limit


class TestAutoCommentNextTimeLimits:
    """``auto_comment_next`` decorator carries hard + soft time limits."""

    def test_auto_comment_next_has_time_limit_600(self):
        from bot.tasks import auto_comment_next

        assert auto_comment_next.time_limit == 600

    def test_auto_comment_next_has_soft_time_limit_480(self):
        from bot.tasks import auto_comment_next

        assert auto_comment_next.soft_time_limit == 480

    def test_comment_soft_strictly_less_than_hard(self):
        from bot.tasks import auto_comment_next

        assert auto_comment_next.soft_time_limit < auto_comment_next.time_limit
