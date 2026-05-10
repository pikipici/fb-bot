"""Celery application configuration.

Beat schedule covers all recurring work:
* ``collect-all-targets`` — collector cycle (default 30 min).
* ``daily-summary`` — daily digest at 09:00 local (Asia/Jakarta).
* ``weekly-report`` — weekly roll-up Monday 09:00 local.
* ``health-check`` — service health ping every ``HEALTH_INTERVAL_SECONDS``.

Reliability knobs:
* ``task_acks_late=True`` + ``task_reject_on_worker_lost=True`` — if a
  worker crashes mid-task the broker redelivers the message instead of
  silently dropping it.
* ``broker_connection_retry_on_startup`` — waits for Redis if it starts
  after the worker (common when systemd order-of-start is loose).
"""

from __future__ import annotations

import os

import sentry_sdk
from celery import Celery
from celery.schedules import crontab
from sentry_sdk.integrations.celery import CeleryIntegration

# Initialize Sentry for Celery worker (no-op if SENTRY_DSN is empty)
sentry_dsn = os.getenv("SENTRY_DSN", "")
if sentry_dsn:
    sentry_sdk.init(
        dsn=sentry_dsn,
        integrations=[CeleryIntegration()],
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_RATE", "0.1")),
        environment=os.getenv("SENTRY_ENV", "production"),
        release=os.getenv("SENTRY_RELEASE", "fb-bot@0.1.0"),
        send_default_pii=False,
    )

# Redis broker URL from env or default (server uses port 6382)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6382/0")

app = Celery("fb_bot")


def _collect_interval() -> int:
    return int(os.getenv("COLLECT_INTERVAL_SECONDS", "1800"))  # 30 min default


def _scan_interval() -> int:
    return int(os.getenv("SCAN_INTERVAL_SECONDS", "900"))  # 15 min default


def _health_interval() -> int:
    return int(os.getenv("HEALTH_INTERVAL_SECONDS", "300"))  # 5 min default


app.conf.update(
    broker_url=REDIS_URL,
    result_backend=REDIS_URL,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Jakarta",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "collect-all-targets": {
            "task": "bot.tasks.collect_all_targets",
            "schedule": _collect_interval(),
        },
        "scan-all-sources": {
            "task": "bot.tasks.scan_all_sources",
            "schedule": _scan_interval(),
        },
        "health-check": {
            "task": "bot.tasks.health_check",
            "schedule": _health_interval(),
        },
        "daily-summary": {
            "task": "bot.tasks.send_daily_summary",
            "schedule": crontab(hour=9, minute=0),  # 09:00 Asia/Jakarta
        },
        "weekly-report": {
            "task": "bot.tasks.send_weekly_report",
            # Monday 09:00 Asia/Jakarta
            "schedule": crontab(hour=9, minute=0, day_of_week="mon"),
        },
    },
    task_default_retry_delay=60,
    task_max_retries=3,
)

app.autodiscover_tasks(["bot"])
