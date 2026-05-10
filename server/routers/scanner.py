"""Router — Scanner status + manual trigger.

Exposes two endpoints powering the Trending page UX polish:

* ``GET /api/v1/scanner/status`` — any authenticated user. Returns the
  most-recent ``ScannerRun`` row plus an ``is_running`` flag the header
  uses to show ``"scan terakhir: 3 menit lalu · 4 post baru"``.
* ``POST /api/v1/scanner/run-now`` — admin only. Enqueues
  ``scan_all_sources(trigger='manual')`` via Celery so users don't have
  to wait for the 15-minute beat interval.

These deliberately don't duplicate the quota / audit infrastructure:
``ScannerRun`` rows are written by ``scan_all_sources`` itself.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from server.auth import Role, get_current_user, require_role
from server.database import get_db
from server.models import ScannerRun

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scanner", tags=["scanner"])

_admin_only = require_role(Role.ADMIN)


def _serialize_run(run: ScannerRun) -> dict:
    return {
        "id": run.id,
        "task_id": run.task_id,
        "trigger": run.trigger,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": (
            run.finished_at.isoformat() if run.finished_at else None
        ),
        "enabled_sources": run.enabled_sources,
        "successful_scans": run.successful_scans,
        "scan_errors": run.scan_errors,
        "inserted": run.inserted,
        "updated": run.updated,
        "skipped": run.skipped,
        "aborted_reason": run.aborted_reason,
        "error_message": run.error_message,
    }


@router.get("/status")
def get_scanner_status(
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return latest scan run + whether a scan is currently running.

    Response shape::

        {
          "is_running": bool,
          "last_run": {...ScannerRun fields...} | null,
          "last_success": {...ScannerRun fields...} | null
        }

    ``last_run`` is the most recent row regardless of status.
    ``last_success`` is the most recent ``status='success'`` row, useful
    for showing ``"scan terakhir sukses: X menit lalu"`` even when the
    current run is still in flight.
    """
    latest = (
        db.query(ScannerRun).order_by(ScannerRun.id.desc()).first()
    )
    last_success = (
        db.query(ScannerRun)
        .filter(ScannerRun.status == "success")
        .order_by(ScannerRun.id.desc())
        .first()
    )
    is_running = latest is not None and latest.status == "running"
    return {
        "is_running": is_running,
        "last_run": _serialize_run(latest) if latest else None,
        "last_success": (
            _serialize_run(last_success) if last_success else None
        ),
    }


@router.post("/run-now", status_code=202)
def trigger_scan_now(
    user=Depends(_admin_only),
    db: Session = Depends(get_db),
):
    """Enqueue ``scan_all_sources(trigger='manual')`` via Celery.

    Returns 202 with ``{task_id, started_at}``. Idempotency: if a run is
    already in progress we reject with 409 so users don't stack duplicate
    Playwright launches.

    The task itself inserts the ``ScannerRun`` row, so the endpoint only
    needs to enqueue. We could pre-insert here to surface the run in
    ``/status`` faster, but that risks two rows for a single logical
    scan if the Celery enqueue fails.
    """
    # Idempotency guard — don't stack manual triggers while one is running.
    latest = db.query(ScannerRun).order_by(ScannerRun.id.desc()).first()
    if latest is not None and latest.status == "running":
        # Only treat as conflict if the running row is recent (<15 min).
        # Otherwise it's probably a stuck row from a crashed worker.
        started_at = latest.started_at
        if started_at is not None:
            # SQLite reads back naive datetimes even when we wrote
            # with tzinfo. Normalize before comparing.
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
            age_s = (
                datetime.now(timezone.utc) - started_at
            ).total_seconds()
            if age_s < 15 * 60:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Scanner lagi jalan (started {int(age_s)}s lalu) — "
                        f"tunggu selesai dulu."
                    ),
                )

    # Import here so test envs that don't wire Celery can still import
    # the router module.
    from bot.tasks import scan_all_sources

    try:
        async_result = scan_all_sources.delay(trigger="manual")
    except Exception as exc:  # noqa: BLE001
        logger.exception("failed to enqueue manual scan: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=f"Gagal kirim scan ke Celery: {exc}",
        ) from exc

    return {
        "task_id": async_result.id,
        "enqueued_at": datetime.now(timezone.utc).isoformat(),
        "trigger": "manual",
    }
