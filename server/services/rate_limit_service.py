"""Rate Limit Service — rolling-window quota untuk comment sender (Layer 2).

MVP: hardcoded 5 komen / 6 jam default, tapi ``MAX_COMMENTS_PER_WINDOW`` env
var bisa override limit-nya (mis. set ke ``9999`` buat efektif bypass preflight
tanpa ripping out service — rollback-friendly kalau perlu dibalikin lagi).
Schema support multi-account tapi MVP cuma single active FB account jadi quota
global (tidak per ``fb_account_id``).

Usage::

    svc = RateLimitService(db)
    status = svc.check_allowed()
    if not status.allowed:
        raise HTTPException(429, f"resets at {status.resets_at}")

    # Setelah komen terkirim ke FB:
    svc.record_send(
        trending_post_id=post.id,
        comment_text=rendered,
        user_id=current_user.id,
        fb_comment_id=resp.get("id"),
    )

``record_send`` auto-preflight check window untuk ``status='SENT'``; kalau
full, raises :class:`RateLimitExceededError`. ``status='FAILED'`` selalu
boleh (audit trail, tidak kena quota).

Kalau status SENT, juga auto-flip :class:`TrendingPost.status` ke
``COMMENTED`` biar UI sinkron.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from server.models import CommentHistory, TrendingPost

logger = logging.getLogger(__name__)


# --- MVP constants ---------------------------------------------------------

MAX_COMMENTS_PER_WINDOW: int = 5
"""Default max SENT rows yang boleh masuk dalam window aktif.

Bisa di-override via env ``MAX_COMMENTS_PER_WINDOW`` (int). Invalid/unset →
fallback ke konstanta ini. Lihat ``_max_per_window``.
"""

WINDOW_HOURS: int = 6
"""Durasi rolling window dalam jam."""


def _max_per_window() -> int:
    """Resolve effective per-window limit: env var > constant default.

    Invalid (non-int) atau missing env → fallback ``MAX_COMMENTS_PER_WINDOW``.
    Dibaca tiap call biar bisa di-toggle tanpa restart — sesuai pola
    ``os.getenv`` di service lain.
    """
    raw = os.getenv("MAX_COMMENTS_PER_WINDOW")
    if raw is None or raw == "":
        return MAX_COMMENTS_PER_WINDOW
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "MAX_COMMENTS_PER_WINDOW=%r tidak valid int, pakai default %d",
            raw,
            MAX_COMMENTS_PER_WINDOW,
        )
        return MAX_COMMENTS_PER_WINDOW


# --- errors ----------------------------------------------------------------


class RateLimitServiceError(Exception):
    """Base class untuk rate-limit service errors."""


class RateLimitExceededError(RateLimitServiceError):
    """Raised kalau record_send dipanggil pas window udah penuh."""


# --- DTO -------------------------------------------------------------------


@dataclass(frozen=True)
class QuotaStatus:
    """Snapshot status quota rolling window."""

    allowed: bool
    used: int
    remaining: int
    limit: int
    window_hours: int
    resets_at: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "used": self.used,
            "remaining": self.remaining,
            "limit": self.limit,
            "window_hours": self.window_hours,
            "resets_at": self.resets_at,
        }


# --- service ---------------------------------------------------------------


class RateLimitService:
    """Quota check + send recording dengan single rolling window."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # --- internal helpers ---------------------------------------------------

    def _window_start(self, now: datetime | None = None) -> datetime:
        now = now or datetime.now(timezone.utc)
        return now - timedelta(hours=WINDOW_HOURS)

    def _sent_in_window(self, window_start: datetime) -> list[CommentHistory]:
        """Ambil semua CommentHistory SENT dalam window, ASC by sent_at."""
        rows = (
            self.db.query(CommentHistory)
            .filter(CommentHistory.status == "SENT")
            .filter(CommentHistory.sent_at >= window_start)
            .order_by(CommentHistory.sent_at.asc())
            .all()
        )
        return rows

    def _compute_status(self, rows: list[CommentHistory]) -> QuotaStatus:
        limit = _max_per_window()
        used = len(rows)
        remaining = max(0, limit - used)
        allowed = used < limit
        resets_at: datetime | None = None
        if rows:
            oldest = rows[0].sent_at
            if oldest is not None:
                if oldest.tzinfo is None:
                    oldest = oldest.replace(tzinfo=timezone.utc)
                resets_at = oldest + timedelta(hours=WINDOW_HOURS)
        return QuotaStatus(
            allowed=allowed,
            used=used,
            remaining=remaining,
            limit=limit,
            window_hours=WINDOW_HOURS,
            resets_at=resets_at,
        )

    # --- public API ---------------------------------------------------------

    def check_allowed(self) -> QuotaStatus:
        """Return QuotaStatus tanpa mutate state. Non-raising."""
        rows = self._sent_in_window(self._window_start())
        return self._compute_status(rows)

    def window_stats(self) -> dict[str, Any]:
        """Convenience wrapper buat router response."""
        return self.check_allowed().to_dict()

    def record_send(
        self,
        *,
        trending_post_id: int,
        comment_text: str,
        user_id: int | None = None,
        fb_comment_id: str | None = None,
        status: str = "SENT",
        error_message: str | None = None,
    ) -> CommentHistory:
        """Insert CommentHistory row.

        Preflight: kalau ``status='SENT'`` dan window udah penuh, raises
        :class:`RateLimitExceededError`. FAILED rows selalu diizinkan.

        Side effect: SENT row auto-flip TrendingPost.status ke COMMENTED.
        """
        if status == "SENT":
            current = self.check_allowed()
            if not current.allowed:
                raise RateLimitExceededError(
                    f"quota habis bro: {current.used}/{current.limit} "
                    f"komen dalam {current.window_hours}h"
                )

        row = CommentHistory(
            trending_post_id=trending_post_id,
            user_id=user_id,
            comment_text=comment_text,
            fb_comment_id=fb_comment_id,
            status=status,
            error_message=error_message,
        )
        self.db.add(row)

        if status == "SENT":
            post = (
                self.db.query(TrendingPost)
                .filter(TrendingPost.id == trending_post_id)
                .first()
            )
            if post is not None:
                post.status = "COMMENTED"

        self.db.commit()
        self.db.refresh(row)
        return row
