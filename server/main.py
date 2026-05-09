"""FastAPI Backend — main application."""

import os
from pathlib import Path

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from server.routers import approvals, auth, drafts, health, posts, reports, settings, stats

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

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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

# Serve dashboard static files (production build)
DASHBOARD_DIR = Path(__file__).parent.parent / "dashboard" / "dist"
if DASHBOARD_DIR.exists() and (DASHBOARD_DIR / "index.html").exists():
    from starlette.responses import Response

    app.mount("/assets", StaticFiles(directory=str(DASHBOARD_DIR / "assets")), name="assets")

    @app.middleware("http")
    async def serve_spa_middleware(request, call_next):
        """Serve React SPA for non-API paths."""
        path = request.url.path

        # Let API and docs routes pass through
        if path.startswith("/api") or path.startswith("/openapi"):
            return await call_next(request)

        # Try to serve static file from dist
        file_path = DASHBOARD_DIR / path.lstrip("/")
        if file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))

        # Fallback to index.html (SPA client-side routing)
        return FileResponse(str(DASHBOARD_DIR / "index.html"))

