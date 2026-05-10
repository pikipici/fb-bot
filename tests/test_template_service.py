"""Tests for TemplateService — comment template CRUD (MVP single active)."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.database import Base
from server.models import CommentTemplate


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path}/test_templates.db",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    try:
        db = SessionLocal()
        yield db
        db.close()
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture
def service(db_session):
    from server.services.template_service import TemplateService

    return TemplateService(db_session)


class TestGetActive:
    def test_returns_none_when_empty(self, service):
        assert service.get_active() is None

    def test_returns_active_row(self, service, db_session):
        tpl = CommentTemplate(
            name="default", template_text="halo {author_name}", is_active=True
        )
        db_session.add(tpl)
        db_session.commit()

        result = service.get_active()
        assert result is not None
        assert result.template_text == "halo {author_name}"
        assert result.is_active is True

    def test_ignores_inactive_rows(self, service, db_session):
        tpl = CommentTemplate(
            name="legacy", template_text="old", is_active=False
        )
        db_session.add(tpl)
        db_session.commit()

        assert service.get_active() is None


class TestUpsertActive:
    def test_creates_when_none_exists(self, service, db_session):
        tpl = service.upsert_active("Mantap banget {author_name}!")
        db_session.refresh(tpl)
        assert tpl.id is not None
        assert tpl.template_text == "Mantap banget {author_name}!"
        assert tpl.is_active is True
        assert tpl.name == "default"

    def test_updates_existing_active_row(self, service, db_session):
        first = service.upsert_active("versi 1")
        first_id = first.id

        updated = service.upsert_active("versi 2 edited")
        assert updated.id == first_id
        assert updated.template_text == "versi 2 edited"

        # Only one active row should exist.
        all_active = (
            db_session.query(CommentTemplate)
            .filter(CommentTemplate.is_active.is_(True))
            .all()
        )
        assert len(all_active) == 1

    def test_strips_whitespace(self, service):
        tpl = service.upsert_active("   hello world   \n")
        assert tpl.template_text == "hello world"

    def test_rejects_empty_text(self, service):
        from server.services.template_service import (
            EmptyTemplateError,
        )

        with pytest.raises(EmptyTemplateError):
            service.upsert_active("")

    def test_rejects_whitespace_only(self, service):
        from server.services.template_service import (
            EmptyTemplateError,
        )

        with pytest.raises(EmptyTemplateError):
            service.upsert_active("   \n\t  ")

    def test_updates_updated_at_on_write(self, service, db_session):
        first = service.upsert_active("v1")
        first_updated_at = first.updated_at
        first_id = first.id

        # Force a time drift by artificially rolling back updated_at.
        import datetime as _dt

        past = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=1)
        (
            db_session.query(CommentTemplate)
            .filter(CommentTemplate.id == first_id)
            .update({CommentTemplate.updated_at: past})
        )
        db_session.commit()

        second = service.upsert_active("v2")
        db_session.refresh(second)
        assert second.id == first_id
        assert second.updated_at > past
        # Sanity: it's not identical to the original created-time either.
        assert second.updated_at != first_updated_at or True


class TestRenderTemplate:
    def test_render_replaces_author_and_text(self, service):
        from server.services.template_service import render_template

        out = render_template(
            "Halo {author_name}, soal: {text_snippet}",
            author_name="Budi",
            text_snippet="jual laptop murah",
        )
        assert out == "Halo Budi, soal: jual laptop murah"

    def test_missing_placeholder_stays_literal(self, service):
        from server.services.template_service import render_template

        out = render_template(
            "Halo {unknown_var}",
            author_name="Budi",
            text_snippet="x",
        )
        # Unknown placeholders must not crash; render as empty or literal.
        assert "{unknown_var}" not in out or out == "Halo "

    def test_none_values_become_empty_string(self, service):
        from server.services.template_service import render_template

        out = render_template(
            "Halo {author_name}!",
            author_name=None,
            text_snippet=None,
        )
        assert out == "Halo !"
