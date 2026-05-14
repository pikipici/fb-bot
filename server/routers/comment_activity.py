"""Router — informational comment activity counter (Layer 2 UX).

- ``GET /api/v1/comment-activity/today`` — any authenticated user. Returns
  today's WIB (Asia/Jakarta) calendar-day count of ``SENT`` comments.
  Replaces the rolling-window quota widget with a simple
  "Komen hari ini: X" readout in the dashboard header.

Response envelope::

    {
      "count_today": 12,
      "date": "2026-05-13",
      "tz": "Asia/Jakarta"
    }

The rate-limit gate itself (``/api/v1/rate-limit/status``) is still served
for backwards-compat/rollback. In production the preflight is bypassed by
setting ``MAX_COMMENTS_PER_WINDOW=9999`` on the server env.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from server.auth import get_current_user
from server.database import get_db
from server.services.comment_activity_service import CommentActivityService

router = APIRouter(prefix="/comment-activity", tags=["comment-activity"])


@router.get("/today")
def get_today_activity(
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return today's WIB calendar-day SENT count."""
    return CommentActivityService(db).today_snapshot()
