"""Router — Source management (admin only).

CRUD endpoints for scan sources (home_feed / group / page). Every
endpoint is admin-gated.

Error mapping:
- :class:`InvalidSourceTypeError` -> 400
- :class:`DuplicateHomeFeedError` -> 409
- :class:`SourceNotFoundError` -> 404
- Validation errors (pydantic) -> 422

Response envelope is ``{"source": {...}}`` for single-row writes and
``{"sources": [...], "total": n}`` for the list endpoint, matching the
existing fb-accounts router style.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from server.auth import Role, require_role
from server.database import get_db
from server.services.source_service import (
    DuplicateHomeFeedError,
    InvalidSourceTypeError,
    SourceNotFoundError,
    SourceService,
)

router = APIRouter(prefix="/sources", tags=["sources"])


_admin_only = require_role(Role.ADMIN)


class CreateSourceRequest(BaseModel):
    type: str = Field(..., description="home_feed | group | page")
    label: str = Field(..., min_length=1, max_length=200)
    url: str | None = Field(default=None, max_length=500)
    fb_entity_id: str | None = Field(default=None, max_length=100)
    keywords_include: list[str] = Field(default_factory=list)
    keywords_exclude: list[str] = Field(default_factory=list)
    enabled: bool = True


class UpdateSourceRequest(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=200)
    url: str | None = Field(default=None, max_length=500)
    fb_entity_id: str | None = Field(default=None, max_length=100)
    keywords_include: list[str] | None = None
    keywords_exclude: list[str] | None = None
    enabled: bool | None = None


@router.get("")
def list_sources(
    enabled_only: bool = False,
    user=Depends(_admin_only),
    db: Session = Depends(get_db),
):
    svc = SourceService(db)
    sources = svc.list_sources(enabled_only=enabled_only)
    return {
        "sources": [svc.to_dict(s) for s in sources],
        "total": len(sources),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_source(
    payload: CreateSourceRequest,
    user=Depends(_admin_only),
    db: Session = Depends(get_db),
):
    svc = SourceService(db)
    try:
        source = svc.create_source(
            type=payload.type,
            label=payload.label,
            url=payload.url,
            fb_entity_id=payload.fb_entity_id,
            keywords_include=payload.keywords_include,
            keywords_exclude=payload.keywords_exclude,
            enabled=payload.enabled,
        )
    except InvalidSourceTypeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DuplicateHomeFeedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"source": svc.to_dict(source)}


@router.get("/{source_id}")
def get_source(
    source_id: int,
    user=Depends(_admin_only),
    db: Session = Depends(get_db),
):
    svc = SourceService(db)
    source = svc.get_source(source_id)
    if source is None:
        raise HTTPException(
            status_code=404, detail=f"Source {source_id} gak ketemu."
        )
    return {"source": svc.to_dict(source)}


@router.patch("/{source_id}")
def update_source(
    source_id: int,
    payload: UpdateSourceRequest,
    user=Depends(_admin_only),
    db: Session = Depends(get_db),
):
    svc = SourceService(db)
    try:
        source = svc.update_source(
            source_id,
            label=payload.label,
            url=payload.url,
            fb_entity_id=payload.fb_entity_id,
            keywords_include=payload.keywords_include,
            keywords_exclude=payload.keywords_exclude,
            enabled=payload.enabled,
        )
    except SourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"source": svc.to_dict(source)}


@router.post("/{source_id}/toggle")
def toggle_source(
    source_id: int,
    user=Depends(_admin_only),
    db: Session = Depends(get_db),
):
    svc = SourceService(db)
    try:
        source = svc.toggle_enabled(source_id)
    except SourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"source": svc.to_dict(source)}


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(
    source_id: int,
    user=Depends(_admin_only),
    db: Session = Depends(get_db),
):
    svc = SourceService(db)
    try:
        svc.delete_source(source_id)
    except SourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
