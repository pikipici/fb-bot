"""Celery application configuration."""

import os

import sentry_sdk
from celery import Celery
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
    worker_prefetch_multiplier=1,
    # Beat schedule
    beat_schedule={
        "collect-all-targets": {
            "task": "bot.tasks.collect_all_targets",
            "schedule": int(os.getenv("COLLECT_INTERVAL_SECONDS", "1800")),  # 30min default
        },
    },
    # Retry defaults
    task_default_retry_delay=60,
    task_max_retries=3,
)

app.autodiscover_tasks(["bot"])
