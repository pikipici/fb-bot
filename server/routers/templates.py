"""Router — comment template (single active, MVP).

- ``GET /api/v1/template`` — any authenticated user can read.
- ``PUT /api/v1/template`` — admin only.

Response envelope::

    { "template": { ... } | null }
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from server.auth import Role, get_current_user, require_role
from server.database import get_db
from server.services.template_service import (
    EmptyTemplateError,
    TemplateService,
    template_to_dict,
)

router = APIRouter(prefix="/template", tags=["templates"])

_admin_only = require_role(Role.ADMIN)


class UpsertTemplateRequest(BaseModel):
    template_text: str = Field(..., min_length=1, max_length=5_000)


@router.get("")
def get_template(
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = TemplateService(db)
    tpl = service.get_active()
    return {"template": template_to_dict(tpl) if tpl else None}


@router.put("")
def upsert_template(
    payload: UpsertTemplateRequest,
    user=Depends(_admin_only),
    db: Session = Depends(get_db),
):
    service = TemplateService(db)
    try:
        tpl = service.upsert_active(payload.template_text)
    except EmptyTemplateError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )
    return {"template": template_to_dict(tpl)}
