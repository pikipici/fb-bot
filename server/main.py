"""FastAPI Backend — main application.

Security posture:
* CORS allow_methods / allow_headers are explicit lists, not wildcards, so
  ``allow_credentials=True`` is safe. Origins come from ``CORS_ORIGINS``
  (comma-separated). In production the env var must be set; the default
  development origin is accepted only when ``ENV`` is not ``production``.
* The SPA fallback middleware resolves requested paths against
  ``DASHBOARD_DIR`` and rejects anything that escapes the dist root to
  prevent path traversal via ``..`` segments or absolute paths.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from server.routers import (
    approvals,
    auth,
    drafts,
    health,
    posts,
    reports,
    settings,
    stats,
)
# DISABLED: multi-account rotation — using single account from .env
# from server.routers import fb_accounts

logger = logging.getLogger(__name__)


def _is_production() -> bool:
    return os.getenv("ENV", "development").strip().lower() == "production"


def _resolve_cors_origins() -> list[str]:
    raw = os.getenv("CORS_ORIGINS", "").strip()
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    if _is_production():
        raise RuntimeError(
            "CORS_ORIGINS must be set in production (comma-separated list)."
        )
    return ["http://localhost:5173"]


# Initialize Sentry (no-op if SENTRY_DSN is empty)
sentry_dsn = os.getenv("SENTRY_DSN", "")
if sentry_dsn:
    sentry_sdk.init(
        dsn=sentry_dsn,
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_RATE", "0.1")),
        profiles_sample_rate=float(os.getenv("SENTRY_PROFILES_RATE", "0.1")),
        environment=os.getenv("SENTRY_ENV", "production"),
        release=os.getenv("SENTRY_RELEASE", "fb-bot@0.1.0"),
        send_default_pii=False,
    )

app = FastAPI(
    title="FB Engagement Assistant API",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# CORS — explicit methods/headers so credentialed requests are safe.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_resolve_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-Requested-With",
        "Accept",
    ],
)

# Routers
app.include_router(auth.router, prefix="/api/v1", tags=["auth"])
app.include_router(health.router, prefix="/api/v1", tags=["health"])
app.include_router(posts.router, prefix="/api/v1", tags=["posts"])
app.include_router(drafts.router, prefix="/api/v1", tags=["drafts"])
app.include_router(approvals.router, prefix="/api/v1", tags=["approvals"])
app.include_router(stats.router, prefix="/api/v1", tags=["stats"])
app.include_router(settings.router, prefix="/api/v1", tags=["settings"])
app.include_router(reports.router, prefix="/api/v1", tags=["reports"])
# DISABLED: multi-account rotation — using single account from .env
# app.include_router(fb_accounts.router, prefix="/api/v1", tags=["fb-accounts"])

# Serve dashboard static files (production build)
DASHBOARD_DIR = (Path(__file__).parent.parent / "dashboard" / "dist").resolve()
if DASHBOARD_DIR.exists() and (DASHBOARD_DIR / "index.html").exists():
    app.mount(
        "/assets",
        StaticFiles(directory=str(DASHBOARD_DIR / "assets")),
        name="assets",
    )

    def _safe_resolve(request_path: str) -> Path | None:
        """Resolve ``request_path`` inside ``DASHBOARD_DIR``.

        Returns ``None`` when the path is absolute, contains ``..`` that
        escapes the dist root, or otherwise resolves outside the dashboard
        directory. This is the minimum guard against path traversal even
        though Starlette normalizes many of these on its own.
        """
        cleaned = request_path.lstrip("/")
        if not cleaned:
            return None
        candidate = (DASHBOARD_DIR / cleaned).resolve()
        try:
            candidate.relative_to(DASHBOARD_DIR)
        except ValueError:
            return None
        return candidate

    @app.middleware("http")
    async def serve_spa_middleware(request, call_next):  # noqa: ANN001
        """Serve React SPA for non-API paths."""
        path = request.url.path

        if path.startswith("/api") or path.startswith("/openapi"):
            return await call_next(request)

        candidate = _safe_resolve(path)
        if candidate is not None and candidate.is_file():
            return FileResponse(str(candidate))

        # SPA fallback — always serve the dist index for client-side routing.
        return FileResponse(str(DASHBOARD_DIR / "index.html"))
