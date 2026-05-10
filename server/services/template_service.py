"""Template Service — comment template CRUD (MVP single active row).

The schema already supports multi-template, but MVP only exposes one
``is_active=True`` row. ``upsert_active`` enforces the single-active
invariant: any previously active rows get deactivated before the new /
updated row is persisted.

Placeholders ``{author_name}`` and ``{text_snippet}`` are rendered by
:func:`render_template` when the draft is generated. Missing keys fall
back to an empty string rather than raising, because trending posts
occasionally miss one of those attributes and we don't want the draft
flow to hard-fail.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from string import Formatter
from typing import Any

from sqlalchemy.orm import Session

from server.models import CommentTemplate

logger = logging.getLogger(__name__)


class TemplateServiceError(Exception):
    """Base class for template-service domain errors."""


class EmptyTemplateError(TemplateServiceError):
    """Raised when the template text is empty / whitespace-only."""


class TemplateService:
    """CRUD operations for the single active :class:`CommentTemplate`."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # --- read -----------------------------------------------------------

    def get_active(self) -> CommentTemplate | None:
        return (
            self.db.query(CommentTemplate)
            .filter(CommentTemplate.is_active.is_(True))
            .order_by(CommentTemplate.id.desc())
            .first()
        )

    # --- write ----------------------------------------------------------

    def upsert_active(
        self, template_text: str, *, name: str = "default"
    ) -> CommentTemplate:
        """Create or update the single active template row."""
        stripped = (template_text or "").strip()
        if not stripped:
            raise EmptyTemplateError(
                "Template gak boleh kosong bro — isi minimal 1 karakter."
            )

        now = datetime.now(timezone.utc)
        active = self.get_active()
        if active is not None:
            active.template_text = stripped
            active.name = name
            active.updated_at = now
            self.db.commit()
            self.db.refresh(active)
            return active

        # Deactivate any stale rows just in case, then insert fresh.
        (
            self.db.query(CommentTemplate)
            .filter(CommentTemplate.is_active.is_(True))
            .update({CommentTemplate.is_active: False})
        )
        row = CommentTemplate(
            name=name,
            template_text=stripped,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row


# --- rendering -------------------------------------------------------------


class _SafeDict(dict):
    """Dict that returns empty string for any missing key.

    Used by :func:`render_template` so the draft generator never raises
    :class:`KeyError` when the post author or text snippet is missing.
    """

    def __missing__(self, key: str) -> str:
        return ""


def _coerce(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def render_template(template_text: str, /, **kwargs: Any) -> str:
    """Replace ``{placeholder}`` tokens with kwargs (``None`` -> ``""``).

    Unknown placeholders are replaced with an empty string to avoid
    breaking the draft flow for posts with partial metadata.
    """
    safe = _SafeDict({k: _coerce(v) for k, v in kwargs.items()})
    try:
        return Formatter().vformat(template_text or "", (), safe)
    except (ValueError, IndexError):
        # Malformed token (e.g. stray ``{``) — fall back to the raw text.
        logger.debug("render_template: falling back to raw text", exc_info=True)
        return template_text or ""


def template_to_dict(template: CommentTemplate) -> dict[str, Any]:
    return {
        "id": template.id,
        "name": template.name,
        "template_text": template.template_text,
        "is_active": template.is_active,
        "created_at": (
            template.created_at.isoformat() if template.created_at else None
        ),
        "updated_at": (
            template.updated_at.isoformat() if template.updated_at else None
        ),
    }
