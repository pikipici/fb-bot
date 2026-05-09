"""FastAPI Backend — main application."""

import os

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
