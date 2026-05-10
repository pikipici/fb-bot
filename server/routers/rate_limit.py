"""Router — rate limit status (Layer 2 quota banner + preflight check).

- ``GET /api/v1/rate-limit/status`` — any authenticated user. Returns current
  rolling-window quota snapshot for the MVP single-account hardcoded limit.

Response envelope::

    {
      "quota": {
        "allowed": true,
        "used": 2,
        "remaining": 3,
        "limit": 5,
        "window_hours": 6,
        "resets_at": "2026-05-10T15:42:00+00:00" | null
      }
    }
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from server.auth import get_current_user
from server.database import get_db
from server.services.rate_limit_service import RateLimitService

router = APIRouter(prefix="/rate-limit", tags=["rate-limit"])


@router.get("/status")
def get_rate_limit_status(
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = RateLimitService(db)
    stats = service.window_stats()
    resets_at = stats.get("resets_at")
    return {
        "quota": {
            "allowed": stats["allowed"],
            "used": stats["used"],
            "remaining": stats["remaining"],
            "limit": stats["limit"],
            "window_hours": stats["window_hours"],
            "resets_at": resets_at.isoformat() if resets_at else None,
        }
    }
