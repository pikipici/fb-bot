"""Celery application configuration."""

import os

from celery import Celery

# Redis broker URL from env or default
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

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
