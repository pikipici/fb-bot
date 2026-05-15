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
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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
from bot.modules.comment_sender import (
    CheckpointRequiredError,
    CommentSendError,
    send_comment,
)
from bot.modules.source_collector import (
    CookieExpiredError,
    SourceCollectorResult,
    scan_source,
)
from server.database import SessionLocal
from server.crypto import decrypt_cookies

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


def _make_cookie_refresh_callback(db: Session, account_id: int):
    """Build an async callback that persists rotated cookies to DB silently.

    Phase I-B-3 — returns an ``async def`` closure suitable for
    ``scan_source(on_cookies_refresh=...)``. Uses
    ``FBAccountService.refresh_cookies_silent`` which only rewrites the
    encrypted cookie blob (no status / profile mutation), so the callback
    is safe to run even if the account was (concurrently) flipped to
    EXPIRED by another code path.
    """
    from server.services.fb_account_service import FBAccountService

    async def _refresh(new_cookies: dict[str, str]) -> None:
        FBAccountService(db).refresh_cookies_silent(
            account_id, cookies=new_cookies
        )

    return _refresh


# Phase J — Self-rescheduling scan chain.
#
# Beat no longer drives ``scan_all_sources`` directly. Instead each finished
# scan picks its own random countdown via ``apply_async`` so FB anti-bot can't
# track a fixed inter-scan delta. ``scan_watchdog`` (5-min beat) re-arms a
# stale chain. The env knob ``SCAN_SELFSCHED_DISABLED=1`` short-circuits the
# rescheduling for emergency rollback to watchdog-only cadence.


def _enqueue_next_scan(*, source: str = "selfsched") -> float | None:
    """Schedule the next ``scan_all_sources`` with a random countdown.

    Returns the chosen countdown (in seconds) for testability/logging.
    Returns ``None`` (and skips dispatch) when ``SCAN_SELFSCHED_DISABLED=1``.
    """
    if os.getenv("SCAN_SELFSCHED_DISABLED") == "1":
        logger.info(
            "scan self-rescheduling DISABLED via env (SCAN_SELFSCHED_DISABLED), "
            "skipping reschedule"
        )
        return None

    import random

    from bot.celery_app import _scan_max_interval, _scan_min_interval

    countdown = random.uniform(_scan_min_interval(), _scan_max_interval())
    scan_all_sources.apply_async(
        kwargs={"trigger": source},
        countdown=countdown,
    )
    logger.info(
        "scan self-rescheduled: next in %.1fs (%.1f min) trigger=%s",
        countdown,
        countdown / 60.0,
        source,
    )
    return countdown


# Phase I-D — Scanner cadence humanization.
#
# FB anti-bot flags rapid, on-the-second auth rhythms coming from a single
# IP (VPS). Two knobs soften that signal:
#
# * ``_sleep_startup_jitter`` — 0..``_STARTUP_JITTER_MAX_SECONDS`` random
#   delay at the very start of a scan cycle so beat ticks don't all land
#   on the same wall-clock boundary across days.
# * ``_sleep_inter_source`` — 30..90s "think-time" BETWEEN sources in a
#   cycle. Mimics a human idling before clicking the next feed.
#
# Both are broken out as module-level coroutines (not inlined) so tests
# can monkeypatch them out for fast, deterministic runs.
_STARTUP_JITTER_MAX_SECONDS: float = 120.0
_INTER_SOURCE_DELAY_MIN_SECONDS: float = 30.0
_INTER_SOURCE_DELAY_MAX_SECONDS: float = 90.0


async def _sleep_startup_jitter() -> None:
    """Sleep a random 0..2-minute startup jitter."""
    import random

    delay = random.uniform(0.0, _STARTUP_JITTER_MAX_SECONDS)
    logger.info("scan_all_sources startup jitter: %.1fs", delay)
    await asyncio.sleep(delay)


async def _sleep_inter_source() -> None:
    """Sleep a random 30..90s "think-time" between sources."""
    import random

    delay = random.uniform(
        _INTER_SOURCE_DELAY_MIN_SECONDS, _INTER_SOURCE_DELAY_MAX_SECONDS
    )
    logger.info("scan_all_sources inter-source think-time: %.1fs", delay)
    await asyncio.sleep(delay)


async def _scan_enabled_sources(
    sources: list,
    cookies: dict,
    *,
    user_agent: str | None = None,
    viewport: dict[str, int] | None = None,
    on_cookies_refresh=None,
    account_id: int | None = None,
) -> tuple[list[SourceCollectorResult], bool]:
    """Run ``scan_source`` for each source sequentially.

    Returns ``(results, cookie_expired)``. When a ``CookieExpiredError``
    is raised the scan is aborted immediately and we surface the flag
    so the caller can flip the account to ``EXPIRED`` without trying
    the remaining sources.

    ``user_agent`` / ``viewport`` come from
    ``FBAccountService.ensure_fingerprint`` — pinned per-account by the
    orchestrator so every source scan within this run presents the
    exact same fingerprint to FB.

    ``on_cookies_refresh`` (Phase I-B-3) is an async callback invoked
    per source after a successful scan with the captured cookie dict;
    the orchestrator uses it to silently persist any cookies FB rotated
    mid-session so the stored blob stays in lockstep with reality.
    """
    results: list[SourceCollectorResult] = []
    await _sleep_startup_jitter()
    for idx, src in enumerate(sources):
        if idx > 0:
            await _sleep_inter_source()
        source_dict = {
            "id": src.id,
            "type": src.type,
            "label": src.label,
            "url": src.url,
            "fb_entity_id": src.fb_entity_id,
        }
        try:
            scan_kwargs: dict[str, Any] = {}
            if user_agent:
                scan_kwargs["user_agent"] = user_agent
            if viewport:
                scan_kwargs["viewport"] = viewport
            if on_cookies_refresh is not None:
                scan_kwargs["on_cookies_refresh"] = on_cookies_refresh
            if account_id is not None:
                scan_kwargs["account_id"] = account_id
            result = await scan_source(source_dict, cookies, **scan_kwargs)
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

    # Phase I-A-3 — pin browser fingerprint (UA + viewport) per-account.
    # Stable across sources within this run and across runs across days —
    # makes FB anti-bot see a consistent device for this session cookie.
    from server.services.fb_account_service import FBAccountService

    fp_svc = FBAccountService(db)
    pinned_ua, pinned_w, pinned_h = fp_svc.ensure_fingerprint(account.id)
    pinned_viewport = {"width": pinned_w, "height": pinned_h}

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
        _scan_enabled_sources(
            sources,
            cookies,
            user_agent=pinned_ua,
            viewport=pinned_viewport,
            on_cookies_refresh=_make_cookie_refresh_callback(db, account.id),
            account_id=account.id,
        )
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

    # Phase L-1 — duplicate chain absorption guard.
    # When two chains co-exist (e.g. operator manually triggered while
    # watchdog had just kicked, or a deploy left an extra _enqueue_next_scan
    # in flight), they will eventually converge and run two parallel
    # Playwright sessions on the same FB account → checkpoint → cookie burn.
    # If a recent ScannerRun is still 'running', this tick is a duplicate:
    # exit early WITHOUT inserting a new row and WITHOUT self-rescheduling
    # so the duplicate branch dies cleanly.
    inflight_window = int(
        os.getenv("SCAN_INFLIGHT_WINDOW_SECONDS", "600")
    )  # 10 min default — safely > realistic scan duration ~2-3 min
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=inflight_window)
    with _db_session() as db:
        in_flight = (
            db.query(ScannerRun)
            .filter(ScannerRun.status == "running")
            .filter(ScannerRun.started_at >= cutoff)
            .order_by(ScannerRun.id.desc())
            .first()
        )
        if in_flight is not None:
            logger.warning(
                "scan_all_sources: duplicate chain detected "
                "(in_flight id=%s started_at=%s trigger=%s), "
                "absorbing branch (no new row, no reschedule)",
                in_flight.id,
                in_flight.started_at,
                trigger,
            )
            return {
                "aborted": True,
                "reason": "duplicate_chain",
                "in_flight_id": in_flight.id,
            }

    run_id: int | None = None
    with _db_session() as db:
        run = ScannerRun(
            task_id=str(task_id) if task_id else None,
            # Phase J — whitelist expanded for self-rescheduling chain.
            trigger=(
                trigger
                if trigger in ("beat", "manual", "selfsched", "watchdog")
                else "beat"
            ),
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
        # Phase J — even on crash, keep the chain alive so watchdog isn't
        # the only thing rescuing us. Worst case ``_enqueue_next_scan``
        # itself blows up; that's why we swallow exceptions here.
        try:
            _enqueue_next_scan()
        except Exception:  # noqa: BLE001
            logger.exception("scan_all_sources reschedule failed (post-crash)")
        return {"aborted": True, "reason": "exception", "error": str(exc)}

    _finalize_scanner_run(
        run_id,
        status="failed" if summary.get("aborted") else "success",
        summary=summary,
    )
    # Phase J — happy-path chain continuation.
    try:
        _enqueue_next_scan()
    except Exception:  # noqa: BLE001
        logger.exception("scan_all_sources reschedule failed (post-success)")
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


# Phase J-3 — watchdog safety net.
#
# The self-rescheduling chain breaks if a worker crashes after
# ``_finalize_scanner_run`` but before ``_enqueue_next_scan`` lands the next
# message in the broker, or if Redis loses the queued message, or if someone
# manually purges the celery queue. Beat fires this watchdog every 5 minutes
# (default) to detect that and re-arm the chain. Cheap query, idempotent.


@app.task
def scan_watchdog():  # noqa: ANN201
    """Detect a stale scan chain and re-kick if needed.

    Returns a dict ``{action, reason, [idle_seconds]}`` for observability.
    Logic:
      * scan currently running → skip (no double-trigger)
      * no ScannerRun history at all → kick (bootstrap)
      * last run finished/started > ``SCAN_MAX_IDLE_SECONDS`` ago → kick
      * otherwise → skip (chain healthy)

    Never raises — defensive against DB/broker hiccups.
    """
    from server.models import ScannerRun

    max_idle = int(os.getenv("SCAN_MAX_IDLE_SECONDS", "1800"))  # 30 min default
    # Phase L-2 — reap zombie 'running' rows older than this threshold.
    # Worker crashes mid-scan (Block detected, OOM, kill -9) leave the row
    # stuck and the L-1 inflight guard would refuse to start fresh scans.
    running_timeout = int(
        os.getenv("SCAN_RUNNING_TIMEOUT_SECONDS", "600")
    )  # 10 min default — generous vs realistic 2-3 min scans

    with _db_session() as db:
        # Phase L-2: reap zombie running rows BEFORE deciding chain health.
        zombie_cutoff = datetime.now(timezone.utc) - timedelta(
            seconds=running_timeout
        )
        zombies = (
            db.query(ScannerRun)
            .filter(ScannerRun.status == "running")
            .filter(ScannerRun.started_at < zombie_cutoff)
            .all()
        )
        if zombies:
            now = datetime.now(timezone.utc)
            for zombie in zombies:
                logger.warning(
                    "scan_watchdog: reaping zombie ScannerRun id=%s "
                    "(started_at=%s, age=%.1fs > %ds)",
                    zombie.id,
                    zombie.started_at,
                    (now - zombie.started_at.replace(
                        tzinfo=timezone.utc
                        if zombie.started_at.tzinfo is None
                        else zombie.started_at.tzinfo
                    )).total_seconds()
                    if zombie.started_at
                    else -1,
                    running_timeout,
                )
                zombie.status = "failed"
                zombie.aborted_reason = "watchdog_zombie_reap"
                zombie.finished_at = now
                if not zombie.error_message:
                    zombie.error_message = (
                        f"reaped by watchdog after {running_timeout}s without finalize"
                    )
            db.commit()

        # Re-query AFTER reaping so we see the post-reap state.
        running = (
            db.query(ScannerRun)
            .filter(ScannerRun.status == "running")
            .first()
        )
        if running:
            logger.debug(
                "scan_watchdog: scan currently running (id=%s), skip",
                running.id,
            )
            return {"action": "skip", "reason": "running"}

        last = (
            db.query(ScannerRun)
            .order_by(ScannerRun.id.desc())
            .first()
        )

    if last is None:
        logger.info("scan_watchdog: no ScannerRun history, kicking bootstrap")
        scan_all_sources.apply_async(kwargs={"trigger": "watchdog"})
        return {"action": "kick", "reason": "no_history"}

    # Use finished_at when present, fall back to started_at (mid-flight crash).
    pivot = last.finished_at or last.started_at
    if pivot is None:
        # Truly malformed row — be conservative and kick.
        logger.warning(
            "scan_watchdog: ScannerRun id=%s has no started/finished, kicking",
            last.id,
        )
        scan_all_sources.apply_async(kwargs={"trigger": "watchdog"})
        return {"action": "kick", "reason": "malformed"}

    # Coerce naive → UTC so comparison doesn't blow up on legacy rows.
    if pivot.tzinfo is None:
        pivot = pivot.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    idle = (now - pivot).total_seconds()

    if idle > max_idle:
        logger.info(
            "scan_watchdog: chain stale (idle=%.1fs > %ds), kicking",
            idle,
            max_idle,
        )
        scan_all_sources.apply_async(kwargs={"trigger": "watchdog"})
        return {"action": "kick", "reason": "stale", "idle_seconds": idle}

    logger.debug(
        "scan_watchdog: chain healthy (idle=%.1fs ≤ %ds), skip",
        idle,
        max_idle,
    )
    return {"action": "skip", "reason": "fresh", "idle_seconds": idle}


# ---------------------------------------------------------------------------
# Phase K — Auto-Comment self-rescheduling chain
# ---------------------------------------------------------------------------
#
# Mirrors Phase J pattern. Beat does NOT drive ``auto_comment_next`` — the
# task self-reschedules with ``random.uniform(min, max)`` countdown so FB
# anti-bot can't lock onto a fixed inter-comment delta. ``auto_comment_watchdog``
# (5-min beat) re-arms a stalled chain. ``AUTO_COMMENT_DISABLED=1`` env kill
# switch puts the whole pipeline to sleep.


def _enqueue_next_comment(*, source: str = "selfsched") -> float | None:
    """Schedule the next ``auto_comment_next`` with a random countdown.

    Returns the chosen countdown (seconds) for testability/logging. Returns
    ``None`` (and skips dispatch) when ``AUTO_COMMENT_DISABLED=1``.
    """
    if os.getenv("AUTO_COMMENT_DISABLED") == "1":
        logger.info(
            "auto-comment self-rescheduling DISABLED via env "
            "(AUTO_COMMENT_DISABLED), skipping reschedule"
        )
        return None

    import random

    from bot.celery_app import (
        _auto_comment_max_interval,
        _auto_comment_min_interval,
    )

    countdown = random.uniform(
        _auto_comment_min_interval(), _auto_comment_max_interval()
    )
    auto_comment_next.apply_async(
        kwargs={"trigger": source},
        countdown=countdown,
    )
    logger.info(
        "auto-comment self-rescheduled: next in %.1fs (%.1f min) trigger=%s",
        countdown,
        countdown / 60.0,
        source,
    )
    return countdown


def _record_comment_draft(
    db: Session,
    *,
    post_id: int,
    comment_text: str,
) -> None:
    """Phase K-5 — insert a DRAFT CommentHistory row for dry-run mode.

    Defensive: never raises (audit trail is best-effort). Used by the
    dry-run branch in ``auto_comment_next`` so the AI output can be
    reviewed offline. Post status is left untouched (NEW); natural dedup
    happens via the CommentHistory join in
    ``AutoCommentService.pick_next_eligible_post``.
    """
    from server.models import CommentHistory

    try:
        row = CommentHistory(
            trending_post_id=post_id,
            comment_text=comment_text or "",
            status="DRAFT",
            error_message=None,
        )
        db.add(row)
        db.commit()
    except Exception:  # noqa: BLE001 — never let audit failure mask task result
        logger.exception(
            "failed to record auto-comment draft for post=%s", post_id
        )


def _record_comment_failure(
    db: Session,
    *,
    post_id: int,
    comment_text: str,
    error_message: str,
    flip_post_to_skipped: bool = True,
) -> None:
    """Insert a FAILED CommentHistory row + optionally flip the post status.

    Defensive: never raises (audit trail is best-effort). Used by every
    error path in ``auto_comment_next`` so dedup stays correct.
    """
    from server.models import CommentHistory, TrendingPost

    try:
        row = CommentHistory(
            trending_post_id=post_id,
            comment_text=comment_text or "",
            status="FAILED",
            error_message=error_message[:1000],
        )
        db.add(row)
        if flip_post_to_skipped:
            post = (
                db.query(TrendingPost)
                .filter(TrendingPost.id == post_id)
                .first()
            )
            if post is not None and post.status == "NEW":
                post.status = "SKIPPED"
        db.commit()
    except Exception:  # noqa: BLE001 — never let audit failure mask task result
        logger.exception(
            "failed to record auto-comment failure for post=%s", post_id
        )


@app.task
def auto_comment_next(trigger: str = "selfsched"):  # noqa: ANN201
    """Pick + draft + send one auto-comment, then reschedule next.

    Returns ``{action, reason, ...}`` for observability.

    Lifecycle, with self-reschedule firing in ``finally`` on every path
    EXCEPT the kill-switch (which intentionally pauses the chain):
      1. Kill-switch → return ``{action: 'disabled'}`` no reschedule.
      2. Pick eligible post → ``no_eligible`` skip + reschedule.
      3. Pre-check rate limit → ``rate_limited`` skip + reschedule.
      4. Pick ACTIVE FB account → ``no_account`` skip + reschedule.
      5. Generate AI draft → on error, record FAILED + flip post SKIPPED.
      5b. DRY-RUN (Phase K-5): if ``AUTO_COMMENT_DRY_RUN=1`` → record
          DRAFT + return ``{action: 'draft'}``. Send is skipped entirely,
          quota is not burned, post stays NEW (CommentHistory join still
          dedups it on next tick).
      6. Send via Playwright → on cookie expire, flip account EXPIRED;
         on other error, flip post SKIPPED.
      7. On success → record_send SENT (post auto-flips COMMENTED).
    """
    if trigger not in ("selfsched", "watchdog", "manual"):
        logger.warning("auto_comment_next: unexpected trigger=%r", trigger)

    if os.getenv("AUTO_COMMENT_DISABLED") == "1":
        logger.info("auto_comment_next: pipeline disabled via env, paused")
        return {"action": "disabled", "reason": "kill_switch"}

    result: dict[str, Any] = {}

    try:
        with _db_session() as db:
            # 1. Pick eligible post
            from server.services.auto_comment_service import (
                AutoCommentService,
            )

            post = AutoCommentService(db).pick_next_eligible_post()
            if post is None:
                logger.info("auto_comment_next: no eligible post, skipping")
                result = {"action": "skip", "reason": "no_eligible"}
                return result

            post_id = post.id
            post_url = post.post_url or ""

            # 2. Rate limit pre-check
            from server.services.rate_limit_service import RateLimitService

            rate_svc = RateLimitService(db)
            quota = rate_svc.check_allowed()
            if not quota.allowed:
                logger.info(
                    "auto_comment_next: quota exceeded %d/%d, skip post=%s",
                    quota.used,
                    quota.limit,
                    post_id,
                )
                result = {
                    "action": "skip",
                    "reason": "rate_limited",
                    "post_id": post_id,
                }
                return result

            # 3. Active FB account
            account = _pick_active_account(db)
            if account is None:
                logger.info(
                    "auto_comment_next: no ACTIVE FB account, skip post=%s",
                    post_id,
                )
                result = {
                    "action": "skip",
                    "reason": "no_account",
                    "post_id": post_id,
                }
                return result

            account_id = account.id

            try:
                cookies = decrypt_cookies(account.cookies_encrypted or "")
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "auto_comment_next: decrypt cookies failed account=%s",
                    account_id,
                )
                _record_comment_failure(
                    db,
                    post_id=post_id,
                    comment_text="",
                    error_message=f"decrypt_cookies_failed: {exc}",
                )
                result = {
                    "action": "failed",
                    "reason": "decrypt_failed",
                    "post_id": post_id,
                }
                return result

            # 4. AI draft
            from server.services.ai_draft_service import (
                AIDraftService,
                AIDraftServiceError,
            )

            try:
                draft = AIDraftService(db).generate(
                    post_id=post_id, user_id=0
                )
            except AIDraftServiceError as exc:
                logger.warning(
                    "auto_comment_next: ai_draft error post=%s: %s",
                    post_id,
                    exc,
                )
                _record_comment_failure(
                    db,
                    post_id=post_id,
                    comment_text="",
                    error_message=f"ai_draft_error: {exc}",
                )
                result = {
                    "action": "failed",
                    "reason": "ai_draft_error",
                    "post_id": post_id,
                }
                return result
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "auto_comment_next: ai_draft unexpected error post=%s",
                    post_id,
                )
                _record_comment_failure(
                    db,
                    post_id=post_id,
                    comment_text="",
                    error_message=f"ai_draft_unexpected: {exc}",
                )
                result = {
                    "action": "failed",
                    "reason": "ai_draft_error",
                    "post_id": post_id,
                }
                return result

            # 4b. Phase K-5 — dry-run branch: skip Playwright send, log DRAFT.
            from bot.celery_app import _auto_comment_dry_run

            if _auto_comment_dry_run():
                logger.info(
                    "auto_comment_next: DRY_RUN post=%s draft_len=%d "
                    "(send_comment skipped, AI quality validation mode)",
                    post_id,
                    len(draft),
                )
                _record_comment_draft(
                    db, post_id=post_id, comment_text=draft
                )
                result = {
                    "action": "draft",
                    "reason": "dry_run",
                    "post_id": post_id,
                    "draft": draft,
                }
                return result

            # 5. Pin fingerprint, build cookie refresh callback
            from server.services.fb_account_service import FBAccountService

            fp_svc = FBAccountService(db)
            pinned_ua, pinned_w, pinned_h = fp_svc.ensure_fingerprint(
                account_id
            )
            display_name = account.fb_name or account.label or "me"
            refresh_cb = _make_cookie_refresh_callback(db, account_id)

            # 6. Send comment via Playwright
            try:
                send_result = asyncio.run(
                    send_comment(
                        post_url=post_url,
                        comment_text=draft,
                        cookies=cookies,
                        display_name=display_name,
                        user_agent=pinned_ua,
                        viewport={"width": pinned_w, "height": pinned_h},
                        on_cookies_refresh=refresh_cb,
                        account_id=account_id,
                    )
                )
            except CookieExpiredError as exc:
                logger.warning(
                    "auto_comment_next: cookie_expired account=%s post=%s: %s",
                    account_id,
                    post_id,
                    exc,
                )
                _mark_cookies_expired(db, account)
                _record_comment_failure(
                    db,
                    post_id=post_id,
                    comment_text=draft,
                    error_message=f"cookie_expired: {exc}",
                    flip_post_to_skipped=False,
                )
                result = {
                    "action": "failed",
                    "reason": "cookie_expired",
                    "post_id": post_id,
                }
                return result
            except CheckpointRequiredError as exc:
                logger.warning(
                    "auto_comment_next: checkpoint account=%s post=%s: %s",
                    account_id,
                    post_id,
                    exc,
                )
                account.status = "CHECKPOINT"
                db.commit()
                _record_comment_failure(
                    db,
                    post_id=post_id,
                    comment_text=draft,
                    error_message=f"checkpoint: {exc}",
                    flip_post_to_skipped=False,
                )
                result = {
                    "action": "failed",
                    "reason": "checkpoint",
                    "post_id": post_id,
                }
                return result
            except CommentSendError as exc:
                logger.exception(
                    "auto_comment_next: comment_sender_error post=%s",
                    post_id,
                )
                _record_comment_failure(
                    db,
                    post_id=post_id,
                    comment_text=draft,
                    error_message=f"sender_error: {exc}",
                )
                result = {
                    "action": "failed",
                    "reason": "sender_error",
                    "post_id": post_id,
                }
                return result
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "auto_comment_next: unexpected send error post=%s",
                    post_id,
                )
                _record_comment_failure(
                    db,
                    post_id=post_id,
                    comment_text=draft,
                    error_message=f"unexpected_send_error: {exc}",
                )
                result = {
                    "action": "failed",
                    "reason": "unexpected",
                    "post_id": post_id,
                }
                return result

            # 7. Send returned without raising — check soft-failure flag.
            if not getattr(send_result, "success", False):
                err = getattr(send_result, "error", None) or "send_failed"
                logger.warning(
                    "auto_comment_next: soft-fail send post=%s: %s",
                    post_id,
                    err,
                )
                _record_comment_failure(
                    db,
                    post_id=post_id,
                    comment_text=draft,
                    error_message=f"soft_fail: {err}",
                )
                result = {
                    "action": "failed",
                    "reason": "send_soft_fail",
                    "post_id": post_id,
                }
                return result

            # SUCCESS path — record SENT, RateLimitService flips post=COMMENTED.
            try:
                rate_svc.record_send(
                    trending_post_id=post_id,
                    comment_text=draft,
                    user_id=None,
                    fb_comment_id=getattr(send_result, "fb_comment_id", None),
                    status="SENT",
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "auto_comment_next: record_send failed post=%s "
                    "(comment was sent on FB!)",
                    post_id,
                )
                # Best-effort: still mark FAILED so we don't double-send.
                _record_comment_failure(
                    db,
                    post_id=post_id,
                    comment_text=draft,
                    error_message=f"record_send_failed: {exc}",
                    flip_post_to_skipped=False,
                )
                result = {
                    "action": "failed",
                    "reason": "record_send_failed",
                    "post_id": post_id,
                }
                return result

            logger.info(
                "auto_comment_next: SENT post=%s account=%s draft_len=%d",
                post_id,
                account_id,
                len(draft),
            )
            result = {
                "action": "sent",
                "post_id": post_id,
                "account_id": account_id,
                "draft": draft,
            }
            return result
    finally:
        # Phase K — always reschedule (kill-switch already returned above).
        try:
            _enqueue_next_comment(source="selfsched")
        except Exception:  # noqa: BLE001
            logger.exception(
                "auto_comment_next: failed to self-reschedule, watchdog will recover"
            )


@app.task
def auto_comment_watchdog():  # noqa: ANN201
    """Detect a stale auto-comment chain and re-kick if needed.

    Returns ``{action, reason, [idle_seconds]}`` for observability. Mirror
    of ``scan_watchdog`` (Phase J-3) but for the comment chain.

    Logic:
      - Kill switch → skip (don't fight the user's pause).
      - No CommentHistory rows → kick (bootstrap, e.g. fresh deploy).
      - Last sent_at within ``AUTO_COMMENT_MAX_IDLE_SECONDS`` → skip (fresh).
      - Otherwise → kick (stale).

    Never raises — defensive against DB hiccups.
    """
    if os.getenv("AUTO_COMMENT_DISABLED") == "1":
        logger.debug("auto_comment_watchdog: kill-switch on, skip")
        return {"action": "skip", "reason": "kill_switch"}

    from server.models import CommentHistory

    max_idle = int(
        os.getenv("AUTO_COMMENT_MAX_IDLE_SECONDS", "1800")
    )  # 30 min default

    with _db_session() as db:
        last = (
            db.query(CommentHistory)
            .order_by(CommentHistory.id.desc())
            .first()
        )

    if last is None:
        logger.info(
            "auto_comment_watchdog: no CommentHistory, kicking bootstrap"
        )
        auto_comment_next.apply_async(kwargs={"trigger": "watchdog"})
        return {"action": "kick", "reason": "no_history"}

    pivot = last.sent_at
    if pivot is None:
        logger.warning(
            "auto_comment_watchdog: history id=%s has no sent_at, kicking",
            last.id,
        )
        auto_comment_next.apply_async(kwargs={"trigger": "watchdog"})
        return {"action": "kick", "reason": "malformed"}

    if pivot.tzinfo is None:
        pivot = pivot.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    idle = (now - pivot).total_seconds()

    if idle > max_idle:
        logger.info(
            "auto_comment_watchdog: chain stale (idle=%.1fs > %ds), kicking",
            idle,
            max_idle,
        )
        auto_comment_next.apply_async(kwargs={"trigger": "watchdog"})
        return {"action": "kick", "reason": "stale", "idle_seconds": idle}

    logger.debug(
        "auto_comment_watchdog: chain healthy (idle=%.1fs ≤ %ds), skip",
        idle,
        max_idle,
    )
    return {"action": "skip", "reason": "fresh", "idle_seconds": idle}
