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
from datetime import datetime, timezone
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
from bot.modules.source_collector import (
    CookieExpiredError,
    SourceCollectorResult,
    scan_source,
)
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


# ---------------------------------------------------------------------------
# Scheduled reports + health check
# ---------------------------------------------------------------------------

def _build_notifier():
    """Build a Notifier, cached-free so feature flag changes take effect."""
    from bot.modules.notifier import Notifier

    return Notifier()


@app.task(bind=True)
def health_check(self):  # noqa: ANN001
    """Periodic health check: counts pending drafts + reports via notifier.

    Never raises — the beat task is best-effort. Failures are logged
    and, when the notifier is configured, surfaced as a service-health
    alert.
    """
    from server.models import Draft

    try:
        with _db_session() as db:
            pending = (
                db.query(Draft).filter(Draft.status == "PENDING_REVIEW").count()
            )
        summary = {"status": "healthy", "pending_drafts": pending}
        logger.info("health_check: %s", summary)
        return summary
    except Exception as exc:  # noqa: BLE001
        logger.error("health_check failed: %s", exc)
        try:
            notifier = _build_notifier()
            asyncio.run(
                notifier.notify_service_health(
                    "fb-bot", "unhealthy", detail=str(exc)[:200]
                )
            )
        except Exception:  # noqa: BLE001
            logger.exception("failed to surface health alert")
        return {"status": "unhealthy", "error": str(exc)}


@app.task(bind=True)
def send_daily_summary(self):  # noqa: ANN001
    """Send the daily digest. Safe to run manually from the dashboard."""
    from sqlalchemy import func
    from datetime import timedelta
    from server.models import Draft, Post

    try:
        with _db_session() as db:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
            stats = {
                "posts_collected": db.query(Post)
                .filter(Post.collected_at >= cutoff)
                .count(),
                "posts_queued": db.query(Post)
                .filter(Post.status == "QUEUED", Post.collected_at >= cutoff)
                .count(),
                "drafts_created": db.query(Draft)
                .filter(Draft.created_at >= cutoff)
                .count(),
                "drafts_approved": db.query(Draft)
                .filter(Draft.status == "APPROVED", Draft.created_at >= cutoff)
                .count(),
                "drafts_rejected": db.query(Draft)
                .filter(Draft.status == "REJECTED", Draft.created_at >= cutoff)
                .count(),
            }
        asyncio.run(_build_notifier().send_daily_summary(stats))
        return {"status": "sent", "stats": stats}
    except Exception as exc:  # noqa: BLE001
        logger.error("send_daily_summary failed: %s", exc)
        return {"status": "error", "error": str(exc)}


@app.task(bind=True)
def send_weekly_report(self):  # noqa: ANN001
    """Send the weekly report. Safe to run manually from the dashboard."""
    from datetime import timedelta
    from server.models import Draft, Post

    try:
        with _db_session() as db:
            cutoff = datetime.now(timezone.utc) - timedelta(days=7)
            total_drafts = (
                db.query(Draft).filter(Draft.created_at >= cutoff).count()
            )
            approved = (
                db.query(Draft)
                .filter(
                    Draft.status == "APPROVED", Draft.created_at >= cutoff
                )
                .count()
            )
            rejected = (
                db.query(Draft)
                .filter(
                    Draft.status == "REJECTED", Draft.created_at >= cutoff
                )
                .count()
            )
            stats = {
                "total_posts": db.query(Post)
                .filter(Post.collected_at >= cutoff)
                .count(),
                "total_drafts": total_drafts,
                "approval_rate": (approved / total_drafts * 100)
                if total_drafts
                else 0.0,
                "edit_rate": 0.0,
                "reject_rate": (rejected / total_drafts * 100)
                if total_drafts
                else 0.0,
            }
        asyncio.run(_build_notifier().send_weekly_report(stats))
        return {"status": "sent", "stats": stats}
    except Exception as exc:  # noqa: BLE001
        logger.error("send_weekly_report failed: %s", exc)
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Layer 1 scanner — source-based trending scan (cookie session)
# ---------------------------------------------------------------------------


def _pick_active_account(db: Session):
    """Return the ACTIVE FB account with encrypted cookies, else None."""
    from server.models import FBAccount

    return (
        db.query(FBAccount)
        .filter(
            FBAccount.status == "ACTIVE",
            FBAccount.cookies_encrypted.isnot(None),
        )
        .order_by(FBAccount.id)
        .first()
    )


def _mark_cookies_expired(db: Session, account) -> None:
    account.status = "EXPIRED"
    account.cookies_expired_at = datetime.now(timezone.utc)
    db.commit()


async def _scan_enabled_sources(
    sources: list,
    cookies: dict,
) -> tuple[list[SourceCollectorResult], bool]:
    """Run ``scan_source`` for each source sequentially.

    Returns ``(results, cookie_expired)``. When a ``CookieExpiredError``
    is raised the scan is aborted immediately and we surface the flag
    so the caller can flip the account to ``EXPIRED`` without trying
    the remaining sources.
    """
    results: list[SourceCollectorResult] = []
    for src in sources:
        source_dict = {
            "id": src.id,
            "type": src.type,
            "label": src.label,
            "url": src.url,
            "fb_entity_id": src.fb_entity_id,
        }
        try:
            result = await scan_source(source_dict, cookies)
        except CookieExpiredError as exc:
            logger.warning(
                "scan aborted — cookie expired on source %s: %s",
                src.id,
                exc,
            )
            return results, True
        results.append(result)
    return results, False


def _run_scan_all_sources(db: Session) -> dict[str, Any]:
    """Core scan orchestration — pulled out so tests can drive it with a
    real DB session without going through Celery.
    """
    from server.crypto import decrypt_cookies
    from server.models import Source
    from server.services.trending_post_service import TrendingPostService

    account = _pick_active_account(db)
    if account is None:
        logger.info("scan_all_sources: no ACTIVE account, skipping")
        return {
            "aborted": True,
            "reason": "no_active_account",
            "enabled_sources": 0,
            "successful_scans": 0,
            "scan_errors": 0,
            "inserted": 0,
            "updated": 0,
            "skipped": 0,
        }

    try:
        cookies = decrypt_cookies(account.cookies_encrypted or "")
    except Exception as exc:  # noqa: BLE001 — bad key or corrupted row
        logger.error("scan_all_sources: failed to decrypt cookies: %s", exc)
        return {
            "aborted": True,
            "reason": "cookie_decrypt_failed",
            "enabled_sources": 0,
            "successful_scans": 0,
            "scan_errors": 0,
            "inserted": 0,
            "updated": 0,
            "skipped": 0,
        }

    sources = (
        db.query(Source)
        .filter(Source.enabled.is_(True))
        .order_by(Source.id)
        .all()
    )

    if not sources:
        return {
            "aborted": False,
            "enabled_sources": 0,
            "successful_scans": 0,
            "scan_errors": 0,
            "inserted": 0,
            "updated": 0,
            "skipped": 0,
        }

    results, cookie_expired = asyncio.run(
        _scan_enabled_sources(sources, cookies)
    )

    if cookie_expired:
        _mark_cookies_expired(db, account)
        return {
            "aborted": True,
            "reason": "cookie_expired",
            "enabled_sources": len(sources),
            "successful_scans": sum(1 for r in results if r.success),
            "scan_errors": sum(1 for r in results if not r.success),
            "inserted": 0,
            "updated": 0,
            "skipped": 0,
        }

    # Upsert posts from successful scans, preserving user status.
    svc = TrendingPostService(db)
    inserted = updated = skipped = 0
    successful = 0
    scan_errors = 0
    scan_time = datetime.now(timezone.utc)
    sources_by_id = {s.id: s for s in sources}

    for result in results:
        if not result.success:
            scan_errors += 1
            continue
        successful += 1
        outcome = svc.upsert_batch(result.source_id, result.posts)
        inserted += outcome.inserted
        updated += outcome.updated
        skipped += outcome.skipped
        source_row = sources_by_id.get(result.source_id)
        if source_row is not None:
            source_row.last_scanned_at = scan_time

    db.commit()

    summary = {
        "aborted": False,
        "enabled_sources": len(sources),
        "successful_scans": successful,
        "scan_errors": scan_errors,
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
    }
    logger.info("scan_all_sources summary: %s", summary)
    return summary


@app.task(bind=True, max_retries=2, default_retry_delay=60)
def scan_all_sources(self, trigger: str = "beat"):  # noqa: ANN001
    """Celery beat task — scan every enabled source, upsert trending posts.

    Single-account MVP: picks the first ACTIVE ``FBAccount`` with cookies
    set and uses those for every scan. If any scan raises
    :class:`CookieExpiredError` we flip the account to ``EXPIRED`` and
    stop scanning until the user re-connects via the dashboard.

    ``trigger`` is either ``"beat"`` (celery scheduler) or ``"manual"``
    (``POST /scanner/run-now``). It's persisted in the ``scanner_runs``
    audit row so the UI can show which scans were user-triggered.
    """
    from server.models import ScannerRun

    task_id = getattr(self.request, "id", None) if hasattr(self, "request") else None
    logger.info("scan_all_sources task started (trigger=%s id=%s)", trigger, task_id)

    run_id: int | None = None
    with _db_session() as db:
        run = ScannerRun(
            task_id=str(task_id) if task_id else None,
            trigger=trigger if trigger in ("beat", "manual") else "beat",
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = run.id

    try:
        with _db_session() as db:
            summary = _run_scan_all_sources(db)
    except Exception as exc:  # noqa: BLE001
        logger.exception("scan_all_sources crashed: %s", exc)
        _finalize_scanner_run(
            run_id,
            status="failed",
            error_message=str(exc),
        )
        return {"aborted": True, "reason": "exception", "error": str(exc)}

    _finalize_scanner_run(
        run_id,
        status="failed" if summary.get("aborted") else "success",
        summary=summary,
    )
    return summary


def _finalize_scanner_run(
    run_id: int | None,
    *,
    status: str,
    summary: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> None:
    """Patch the ``ScannerRun`` row with finished_at + counters + status.

    Noop when ``run_id`` is None (we failed before the row got inserted,
    which shouldn't happen but we guard anyway).
    """
    if run_id is None:
        return
    from server.models import ScannerRun

    try:
        with _db_session() as db:
            run = db.query(ScannerRun).filter(ScannerRun.id == run_id).first()
            if run is None:
                return
            run.status = status
            run.finished_at = datetime.now(timezone.utc)
            if summary:
                run.enabled_sources = int(summary.get("enabled_sources", 0))
                run.successful_scans = int(summary.get("successful_scans", 0))
                run.scan_errors = int(summary.get("scan_errors", 0))
                run.inserted = int(summary.get("inserted", 0))
                run.updated = int(summary.get("updated", 0))
                run.skipped = int(summary.get("skipped", 0))
                reason = summary.get("reason")
                if reason:
                    run.aborted_reason = str(reason)[:50]
            if error_message:
                run.error_message = error_message
            db.commit()
    except Exception:  # noqa: BLE001 — never let audit failure mask task result
        logger.exception("failed to finalize scanner_run id=%s", run_id)
