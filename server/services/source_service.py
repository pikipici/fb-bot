"""Source Service — CRUD for scan sources (home_feed, group, page).

A ``Source`` represents one place the scanner should look for trending
posts. Three types are supported:

- ``home_feed`` — the authenticated user's own news feed. Only one row
  of this type may exist, and it has no url/fb_entity_id.
- ``group`` — a facebook group. url + fb_entity_id required.
- ``page`` — a facebook page. url + fb_entity_id required.

Keyword filters are stored as JSON strings in the ``keywords_include``
and ``keywords_exclude`` columns. They're normalized on write (trim +
lowercase + dedup) and decoded on read via :meth:`to_dict`. An empty
list is stored as ``NULL`` so SQL queries can cheaply detect the
"no filter" case with ``IS NULL`` predicates.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterable

from sqlalchemy.orm import Session

from server.models import Source

logger = logging.getLogger(__name__)

VALID_TYPES = ("home_feed", "group", "page")


class SourceServiceError(Exception):
    """Base class for source-service domain errors."""


class InvalidSourceTypeError(SourceServiceError):
    """Raised when ``type`` is not one of :data:`VALID_TYPES`."""


class DuplicateHomeFeedError(SourceServiceError):
    """Raised when trying to create a second ``home_feed`` row."""


class SourceNotFoundError(SourceServiceError):
    """Raised when the target source id doesn't exist."""


def _normalize_keywords(kws: Iterable[str] | None) -> str | None:
    """Trim, lowercase, dedup (preserve order) and JSON-encode keywords.

    Returns ``None`` for ``None`` or empty iterable so NULL-aware queries
    keep working.
    """
    if kws is None:
        return None
    seen: set[str] = set()
    cleaned: list[str] = []
    for k in kws:
        if not isinstance(k, str):
            continue
        normalized = k.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(normalized)
    if not cleaned:
        return None
    return json.dumps(cleaned, ensure_ascii=False)


def _decode_keywords(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, str)]


class SourceService:
    """Manage scan sources."""

    def __init__(self, db: Session):
        self.db = db

    # --- create --------------------------------------------------------

    def create_source(
        self,
        *,
        type: str,
        label: str,
        url: str | None = None,
        fb_entity_id: str | None = None,
        keywords_include: Iterable[str] | None = None,
        keywords_exclude: Iterable[str] | None = None,
        enabled: bool = True,
    ) -> Source:
        if type not in VALID_TYPES:
            raise InvalidSourceTypeError(
                f"Source type '{type}' gak valid. Pake salah satu: "
                + ", ".join(VALID_TYPES)
            )

        if type == "home_feed":
            existing = (
                self.db.query(Source).filter(Source.type == "home_feed").first()
            )
            if existing is not None:
                raise DuplicateHomeFeedError(
                    "Home feed cuma boleh satu. Hapus yang lama dulu."
                )
            # home_feed never needs url / entity id
            url = None
            fb_entity_id = None

        source = Source(
            type=type,
            label=(label or "").strip(),
            url=url,
            fb_entity_id=fb_entity_id,
            keywords_include=_normalize_keywords(keywords_include),
            keywords_exclude=_normalize_keywords(keywords_exclude),
            enabled=enabled,
        )
        self.db.add(source)
        self.db.commit()
        self.db.refresh(source)
        logger.info(
            "Created source: type=%s label=%s (id=%d)",
            source.type,
            source.label,
            source.id,
        )
        return source

    # --- read ----------------------------------------------------------

    def get_source(self, source_id: int) -> Source | None:
        return (
            self.db.query(Source).filter(Source.id == source_id).first()
        )

    def list_sources(self, *, enabled_only: bool = False) -> list[Source]:
        query = self.db.query(Source)
        if enabled_only:
            query = query.filter(Source.enabled.is_(True))
        return query.order_by(Source.id).all()

    # --- update --------------------------------------------------------

    def update_source(
        self,
        source_id: int,
        *,
        label: str | None = None,
        url: str | None = None,
        fb_entity_id: str | None = None,
        keywords_include: Iterable[str] | None = None,
        keywords_exclude: Iterable[str] | None = None,
        enabled: bool | None = None,
    ) -> Source:
        source = self.get_source(source_id)
        if source is None:
            raise SourceNotFoundError(f"Source {source_id} gak ketemu.")

        if label is not None:
            source.label = label.strip()
        if url is not None:
            source.url = url
        if fb_entity_id is not None:
            source.fb_entity_id = fb_entity_id
        if keywords_include is not None:
            source.keywords_include = _normalize_keywords(keywords_include)
        if keywords_exclude is not None:
            source.keywords_exclude = _normalize_keywords(keywords_exclude)
        if enabled is not None:
            source.enabled = enabled

        self.db.commit()
        self.db.refresh(source)
        return source

    def toggle_enabled(self, source_id: int) -> Source:
        source = self.get_source(source_id)
        if source is None:
            raise SourceNotFoundError(f"Source {source_id} gak ketemu.")
        source.enabled = not source.enabled
        self.db.commit()
        self.db.refresh(source)
        return source

    # --- delete --------------------------------------------------------

    def delete_source(self, source_id: int) -> None:
        source = self.get_source(source_id)
        if source is None:
            raise SourceNotFoundError(f"Source {source_id} gak ketemu.")
        self.db.delete(source)
        self.db.commit()

    # --- serialization -------------------------------------------------

    def to_dict(self, source: Source) -> dict[str, Any]:
        return {
            "id": source.id,
            "type": source.type,
            "label": source.label,
            "url": source.url,
            "fb_entity_id": source.fb_entity_id,
            "keywords_include": _decode_keywords(source.keywords_include),
            "keywords_exclude": _decode_keywords(source.keywords_exclude),
            "enabled": source.enabled,
            "last_scanned_at": (
                source.last_scanned_at.isoformat()
                if source.last_scanned_at
                else None
            ),
            "created_at": (
                source.created_at.isoformat() if source.created_at else None
            ),
        }
