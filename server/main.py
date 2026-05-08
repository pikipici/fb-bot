"""FastAPI Backend — main application."""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server.routers import approvals, auth, drafts, health, posts, reports, settings, stats

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
