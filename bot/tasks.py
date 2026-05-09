"""Celery tasks — collection and processing."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from bot.celery_app import app
from bot.modules.circuit_breaker import CircuitBreaker
from bot.modules.collector import Collector, CollectorResult
from bot.modules.orchestrator import Orchestrator
from bot.modules.parser import Parser
from bot.modules.pipeline import Pipeline
from bot.modules.rate_guard import RateGuard
from bot.modules.scheduler import TargetScheduler

logger = logging.getLogger(__name__)

# Config paths
CONFIG_DIR = Path(__file__).parent / "config"
TARGETS_PATH = CONFIG_DIR / "targets.json"
RATE_LIMITS_PATH = CONFIG_DIR / "rate_limits.json"


def _load_json(path: Path) -> dict[str, Any]:
    """Load JSON config file."""
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_components() -> tuple[TargetScheduler, Collector, Pipeline, Orchestrator]:
    """Build all components with shared circuit breaker and rate guard."""
    rate_config = _load_json(RATE_LIMITS_PATH)
    circuit_breaker = CircuitBreaker()
    rate_guard = RateGuard(rate_config)
    parser = Parser()

    scheduler = TargetScheduler(
        config_path=TARGETS_PATH,
        circuit_breaker=circuit_breaker,
        rate_guard=rate_guard,
    )

    collector = Collector(
        rate_guard=rate_guard,
        circuit_breaker=circuit_breaker,
        parser=parser,
    )

    pipeline = Pipeline()
    orchestrator = Orchestrator()

    return scheduler, collector, pipeline, orchestrator


@app.task(bind=True, max_retries=3, default_retry_delay=60)
def collect_all_targets(self):
    """Celery task: collect posts from all runnable targets.

    Flow:
    1. Scheduler selects eligible targets
    2. Collector fetches posts from each target
    3. Pipeline scores and filters posts
    4. Orchestrator generates drafts for queued posts
    """
    logger.info("Starting collect_all_targets task")

    scheduler, collector, pipeline, orchestrator = _build_components()
    targets = scheduler.get_runnable_targets()

    if not targets:
        logger.info("No runnable targets found")
        return {"status": "no_targets", "collected": 0, "queued": 0, "drafted": 0}

    logger.info("Found %d runnable targets", len(targets))

    # Run async collector in sync celery context
    results = asyncio.run(_collect_targets(collector, targets))

    # Process collected posts
    total_collected = 0
    total_queued = 0
    total_drafted = 0

    for result in results:
        if not result.success or not result.posts:
            continue

        total_collected += len(result.posts)

        # Run through pipeline
        batch_result = pipeline.process_batch(result.posts)
        queued_posts = [
            p for p in batch_result["results"] if p.get("status") == "QUEUED"
        ]
        total_queued += len(queued_posts)

        # Generate drafts for queued posts
        for post in queued_posts:
            draft_result = orchestrator.process(post)
            if draft_result.get("draft_status") not in ("FAILED", None):
                total_drafted += 1

        # Mark target as run
        scheduler.mark_run(result.target_id)

    summary = {
        "status": "completed",
        "targets_attempted": len(targets),
        "targets_succeeded": sum(1 for r in results if r.success),
        "collected": total_collected,
        "queued": total_queued,
        "drafted": total_drafted,
    }
    logger.info("collect_all_targets completed: %s", summary)
    return summary


async def _collect_targets(
    collector: Collector, targets: list[dict[str, Any]]
) -> list[CollectorResult]:
    """Collect from multiple targets sequentially (to respect rate limits)."""
    results = []
    for target in targets:
        result = await collector.collect_target(target)
        results.append(result)
    return results


@app.task(bind=True, max_retries=2, default_retry_delay=30)
def collect_single_target(self, target_id: str):
    """Celery task: collect posts from a single target by ID.

    Useful for manual triggers from dashboard.
    """
    logger.info("Starting collect_single_target: %s", target_id)

    scheduler, collector, pipeline, orchestrator = _build_components()
    target = scheduler.get_target_by_id(target_id)

    if not target:
        logger.error("Target not found: %s", target_id)
        return {"status": "error", "error": f"Target {target_id} not found"}

    result = asyncio.run(collector.collect_target(target))

    if not result.success:
        logger.warning("Collection failed for %s: %s", target_id, result.error)
        return {"status": "failed", "error": result.error, "blocked": result.blocked}

    # Process through pipeline
    batch_result = pipeline.process_batch(result.posts)
    queued_posts = [
        p for p in batch_result["results"] if p.get("status") == "QUEUED"
    ]

    # Generate drafts
    drafted = 0
    for post in queued_posts:
        draft_result = orchestrator.process(post)
        if draft_result.get("draft_status") not in ("FAILED", None):
            drafted += 1

    scheduler.mark_run(target_id)

    return {
        "status": "completed",
        "target_id": target_id,
        "collected": len(result.posts),
        "queued": len(queued_posts),
        "drafted": drafted,
    }
