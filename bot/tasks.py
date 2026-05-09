"""Celery tasks — collection and processing.

Tasks own the DB session lifecycle: open a ``SessionLocal()`` per task run,
build the orchestrator with that session, and close it in ``finally``. The
pipeline (filter → score → save → draft) is executed inside the orchestrator
so tasks do NOT run the pipeline a second time.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy.orm import Session

from bot.celery_app import app
from bot.modules.circuit_breaker import CircuitBreaker
from bot.modules.collector import Collector, CollectorResult
from bot.modules.orchestrator import Orchestrator
from bot.modules.parser import Parser
from bot.modules.rate_guard import RateGuard
from bot.modules.scheduler import TargetScheduler
from server.database import SessionLocal

logger = logging.getLogger(__name__)

# Config paths
CONFIG_DIR = Path(__file__).parent / "config"
TARGETS_PATH = CONFIG_DIR / "targets.json"
RATE_LIMITS_PATH = CONFIG_DIR / "rate_limits.json"
FEATURE_FLAGS_PATH = CONFIG_DIR / "feature_flags.json"


def _load_json(path: Path) -> dict[str, Any]:
    """Load a JSON config file, returning ``{}`` if missing."""
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@contextmanager
def _db_session() -> Iterator[Session]:
    """Open a SessionLocal() for the duration of a Celery task.

    Rolls back on exception and always closes the session. Extracted as a
    context manager so both ``collect_all_targets`` and ``collect_single_target``
    share identical lifecycle semantics.
    """
    session: Session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _build_scheduling_components() -> tuple[TargetScheduler, Collector]:
    """Build scheduler + collector (stateless with respect to the DB).

    These components share a single ``CircuitBreaker`` and ``RateGuard`` per
    task run so that rate decisions stay consistent across the collector and
    scheduler callers.
    """
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
    return scheduler, collector


def _build_orchestrator(db: Session) -> Orchestrator:
    """Build an Orchestrator bound to the given DB session.

    Honors the ``ai_draft_enabled`` feature flag from
    ``bot/config/feature_flags.json``.
    """
    flags = _load_json(FEATURE_FLAGS_PATH)
    return Orchestrator(db=db, ai_enabled=bool(flags.get("ai_draft_enabled", False)))


# Back-compat shim for tests that patch ``bot.tasks._build_components``.
# New code should use the specialized builders above.
def _build_components(
    db: Session | None = None,
) -> tuple[TargetScheduler, Collector, Orchestrator]:
    """Build (scheduler, collector, orchestrator).

    If ``db`` is ``None``, a fresh ``SessionLocal()`` is attached to the
    orchestrator. The caller is responsible for closing that session.
    """
    scheduler, collector = _build_scheduling_components()
    session = db if db is not None else SessionLocal()
    orchestrator = _build_orchestrator(session)
    return scheduler, collector, orchestrator


async def _collect_targets(
    collector: Collector, targets: list[dict[str, Any]]
) -> list[CollectorResult]:
    """Collect from multiple targets sequentially (respects rate limits)."""
    results: list[CollectorResult] = []
    for target in targets:
        result = await collector.collect_target(target)
        results.append(result)
    return results


def _summarize_orchestrator_output(result: dict[str, Any]) -> tuple[int, int]:
    """Return ``(queued, drafted)`` from an orchestrator summary dict."""
    queued = int(result.get("queued", 0))
    drafted = int(result.get("drafts_created", 0))
    return queued, drafted


@app.task(bind=True, max_retries=3, default_retry_delay=60)
def collect_all_targets(self):  # noqa: ANN001  # Celery bind=True signature
    """Celery task: collect posts from all runnable targets.

    Flow:
    1. Scheduler selects eligible targets.
    2. Collector fetches posts from each target.
    3. Orchestrator runs pipeline + DB save + draft generation.
    """
    logger.info("Starting collect_all_targets task")

    scheduler, collector = _build_scheduling_components()
    targets = scheduler.get_runnable_targets()

    if not targets:
        logger.info("No runnable targets found")
        return {"status": "no_targets", "collected": 0, "queued": 0, "drafted": 0}

    logger.info("Found %d runnable targets", len(targets))

    # Collect (async) before we open the DB session to minimize lock time.
    results = asyncio.run(_collect_targets(collector, targets))

    total_collected = 0
    total_queued = 0
    total_drafted = 0

    with _db_session() as db:
        orchestrator = _build_orchestrator(db)

        for result in results:
            if not result.success or not result.posts:
                continue

            total_collected += len(result.posts)

            summary = orchestrator.process_collected_posts(result.posts)
            queued, drafted = _summarize_orchestrator_output(summary)
            total_queued += queued
            total_drafted += drafted

            scheduler.mark_run(result.target_id)

    summary_out = {
        "status": "completed",
        "targets_attempted": len(targets),
        "targets_succeeded": sum(1 for r in results if r.success),
        "collected": total_collected,
        "queued": total_queued,
        "drafted": total_drafted,
    }
    logger.info("collect_all_targets completed: %s", summary_out)
    return summary_out


@app.task(bind=True, max_retries=2, default_retry_delay=30)
def collect_single_target(self, target_id: str):  # noqa: ANN001
    """Celery task: collect posts from a single target by ID.

    Useful for manual triggers from the dashboard.
    """
    logger.info("Starting collect_single_target: %s", target_id)

    scheduler, collector = _build_scheduling_components()
    target = scheduler.get_target_by_id(target_id)

    if not target:
        logger.error("Target not found: %s", target_id)
        return {"status": "error", "error": f"Target {target_id} not found"}

    result = asyncio.run(collector.collect_target(target))

    if not result.success:
        logger.warning("Collection failed for %s: %s", target_id, result.error)
        return {
            "status": "failed",
            "error": result.error,
            "blocked": result.blocked,
        }

    with _db_session() as db:
        orchestrator = _build_orchestrator(db)
        summary = orchestrator.process_collected_posts(result.posts)

    queued, drafted = _summarize_orchestrator_output(summary)
    scheduler.mark_run(target_id)

    return {
        "status": "completed",
        "target_id": target_id,
        "collected": len(result.posts),
        "queued": queued,
        "drafted": drafted,
    }
