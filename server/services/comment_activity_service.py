"""Comment Activity Service — calendar-day WIB counter (Layer 2 UX).

Replaces the quota-gate UX (5/6h preflight) with an informational
"Komen hari ini: X" widget. Counts ``CommentHistory`` rows with
``status='SENT'`` within the current ``Asia/Jakarta`` calendar day.

The rate-limit gate itself still exists in
:mod:`server.services.rate_limit_service`; it's neutered in production
by setting ``MAX_COMMENTS_PER_WINDOW=9999`` in the server env, so the
preflight pass-throughs. This service is strictly read-only.

Usage::

    svc = CommentActivityService(db)
    count = svc.today_count()
    # or for router payload:
    snap = svc.today_snapshot()
    # → {"count_today": 12, "date": "2026-05-13", "tz": "Asia/Jakarta"}

WIB = UTC+7, no DST. Calendar day boundary ``00:00 WIB`` corresponds
to ``17:00 UTC`` of the previous UTC calendar day.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from server.models import CommentHistory

logger = logging.getLogger(__name__)

WIB = ZoneInfo("Asia/Jakarta")
"""Fixed Indonesia Western Time zone (UTC+7, no DST)."""


class CommentActivityService:
    """Read-only counter for the "Komen hari ini" header widget."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # --- internal helpers ---------------------------------------------------

    def _today_wib_bounds(
        self, now: datetime | None = None
    ) -> tuple[datetime, datetime, date]:
        """Return ``(start_utc, end_utc, wib_date)`` for the current WIB day.

        Bounds are half-open: ``[start_utc, end_utc)``. ``wib_date`` is the
        calendar date in WIB (what the user sees as "today").
        """
        now_utc = now or datetime.now(timezone.utc)
        now_wib = now_utc.astimezone(WIB)
        today_wib = now_wib.date()
        start_wib = datetime.combine(today_wib, time.min, tzinfo=WIB)
        end_wib = start_wib + timedelta(days=1)
        return (
            start_wib.astimezone(timezone.utc),
            end_wib.astimezone(timezone.utc),
            today_wib,
        )

    # --- public API ---------------------------------------------------------

    def today_count(self, now: datetime | None = None) -> int:
        """Count SENT rows whose ``sent_at`` is inside today's WIB window."""
        start_utc, end_utc, _ = self._today_wib_bounds(now)
        # ``CommentHistory.sent_at`` is stored as UTC (naive or tz-aware via
        # SQLAlchemy ``DateTime(timezone=True)``). Filter on UTC bounds.
        stmt = (
            self.db.query(func.count(CommentHistory.id))
            .filter(CommentHistory.status == "SENT")
            .filter(CommentHistory.sent_at >= start_utc)
            .filter(CommentHistory.sent_at < end_utc)
        )
        return int(stmt.scalar() or 0)

    def today_snapshot(self, now: datetime | None = None) -> dict[str, Any]:
        """Router-friendly dict payload."""
        start_utc, _, wib_date = self._today_wib_bounds(now)
        # `start_utc` not needed here but kept out of touch to reuse bounds.
        _ = start_utc
        return {
            "count_today": self.today_count(now),
            "date": wib_date.isoformat(),
            "tz": "Asia/Jakarta",
        }
