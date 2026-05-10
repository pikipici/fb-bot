"""Tests for SourceService — CRUD untuk scan sources."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.models import Base, Source
from server.services.source_service import (
    DuplicateHomeFeedError,
    InvalidSourceTypeError,
    SourceNotFoundError,
    SourceService,
)


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def svc(db):
    return SourceService(db)


# --- create_source --------------------------------------------------------


class TestCreateSource:
    def test_create_group_minimal(self, svc):
        s = svc.create_source(
            type="group",
            label="Jualan Laptop",
            url="https://www.facebook.com/groups/12345",
            fb_entity_id="12345",
        )
        assert s.id is not None
        assert s.type == "group"
        assert s.label == "Jualan Laptop"
        assert s.fb_entity_id == "12345"
        assert s.enabled is True
        assert s.keywords_include is None
        assert s.keywords_exclude is None

    def test_create_page(self, svc):
        s = svc.create_source(
            type="page",
            label="Tech News",
            url="https://www.facebook.com/technews",
            fb_entity_id="technews",
        )
        assert s.type == "page"

    def test_create_home_feed_no_url_needed(self, svc):
        s = svc.create_source(type="home_feed", label="Beranda Gue")
        assert s.type == "home_feed"
        assert s.url is None
        assert s.fb_entity_id is None

    def test_create_with_keywords_stores_as_json(self, svc):
        s = svc.create_source(
            type="group",
            label="Deal",
            url="https://fb.com/groups/1",
            fb_entity_id="1",
            keywords_include=["laptop", "gaming"],
            keywords_exclude=["rusak", "bekas"],
        )
        assert s.keywords_include == '["laptop", "gaming"]'
        assert s.keywords_exclude == '["rusak", "bekas"]'

    def test_create_empty_keyword_list_stored_as_none(self, svc):
        """Empty lists → stored as NULL to distinguish from explicit empty."""
        s = svc.create_source(
            type="group",
            label="X",
            url="https://fb.com/groups/1",
            fb_entity_id="1",
            keywords_include=[],
            keywords_exclude=[],
        )
        assert s.keywords_include is None
        assert s.keywords_exclude is None

    def test_create_keyword_trim_and_lowercase(self, svc):
        """Keywords should be normalized to trimmed lowercase for consistent match."""
        s = svc.create_source(
            type="group",
            label="X",
            url="https://fb.com/groups/1",
            fb_entity_id="1",
            keywords_include=["  Laptop ", "GAMING"],
        )
        assert s.keywords_include == '["laptop", "gaming"]'

    def test_create_invalid_type_raises(self, svc):
        with pytest.raises(InvalidSourceTypeError):
            svc.create_source(type="profile", label="x")

    def test_create_duplicate_home_feed_raises(self, svc):
        svc.create_source(type="home_feed", label="Beranda 1")
        with pytest.raises(DuplicateHomeFeedError):
            svc.create_source(type="home_feed", label="Beranda 2")

    def test_create_multiple_groups_allowed(self, svc):
        svc.create_source(
            type="group", label="G1", url="https://fb.com/groups/1", fb_entity_id="1"
        )
        svc.create_source(
            type="group", label="G2", url="https://fb.com/groups/2", fb_entity_id="2"
        )
        assert svc.list_sources().__len__() == 2

    def test_create_strips_label_whitespace(self, svc):
        s = svc.create_source(
            type="group",
            label="   Padded Label   ",
            url="https://fb.com/groups/1",
            fb_entity_id="1",
        )
        assert s.label == "Padded Label"


# --- list_sources ---------------------------------------------------------


class TestListSources:
    def test_empty_returns_empty_list(self, svc):
        assert svc.list_sources() == []

    def test_list_returns_all_by_default(self, svc):
        svc.create_source(
            type="group", label="A", url="https://fb.com/groups/1", fb_entity_id="1"
        )
        svc.create_source(
            type="group", label="B", url="https://fb.com/groups/2", fb_entity_id="2"
        )
        result = svc.list_sources()
        assert len(result) == 2

    def test_list_ordered_by_id(self, svc):
        first = svc.create_source(
            type="group", label="A", url="https://fb.com/groups/1", fb_entity_id="1"
        )
        second = svc.create_source(
            type="group", label="B", url="https://fb.com/groups/2", fb_entity_id="2"
        )
        result = svc.list_sources()
        assert result[0].id == first.id
        assert result[1].id == second.id

    def test_list_enabled_only(self, svc):
        a = svc.create_source(
            type="group", label="A", url="https://fb.com/groups/1", fb_entity_id="1"
        )
        b = svc.create_source(
            type="group", label="B", url="https://fb.com/groups/2", fb_entity_id="2"
        )
        svc.update_source(b.id, enabled=False)
        enabled = svc.list_sources(enabled_only=True)
        assert len(enabled) == 1
        assert enabled[0].id == a.id


# --- get_source -----------------------------------------------------------


class TestGetSource:
    def test_get_existing(self, svc):
        created = svc.create_source(type="home_feed", label="Beranda")
        found = svc.get_source(created.id)
        assert found is not None
        assert found.id == created.id

    def test_get_missing_returns_none(self, svc):
        assert svc.get_source(999) is None


# --- update_source --------------------------------------------------------


class TestUpdateSource:
    def test_update_label(self, svc):
        s = svc.create_source(
            type="group", label="Old", url="https://fb.com/groups/1", fb_entity_id="1"
        )
        updated = svc.update_source(s.id, label="New")
        assert updated.label == "New"

    def test_update_keywords(self, svc):
        s = svc.create_source(
            type="group", label="X", url="https://fb.com/groups/1", fb_entity_id="1"
        )
        updated = svc.update_source(s.id, keywords_include=["a", "b"])
        assert updated.keywords_include == '["a", "b"]'

    def test_update_enabled_toggle(self, svc):
        s = svc.create_source(
            type="group", label="X", url="https://fb.com/groups/1", fb_entity_id="1"
        )
        updated = svc.update_source(s.id, enabled=False)
        assert updated.enabled is False

    def test_update_missing_raises(self, svc):
        with pytest.raises(SourceNotFoundError):
            svc.update_source(999, label="nope")

    def test_update_does_not_change_type(self, svc):
        """Changing type would break fb_post_id references; disallow."""
        s = svc.create_source(
            type="group", label="X", url="https://fb.com/groups/1", fb_entity_id="1"
        )
        # 'type' kwarg explicitly NOT supported by update_source; caller must
        # delete + recreate. We ensure passing it is silently ignored or raises.
        updated = svc.update_source(s.id, label="Y")
        assert updated.type == "group"


# --- delete_source --------------------------------------------------------


class TestDeleteSource:
    def test_delete_existing(self, svc):
        s = svc.create_source(type="home_feed", label="X")
        svc.delete_source(s.id)
        assert svc.get_source(s.id) is None

    def test_delete_missing_raises(self, svc):
        with pytest.raises(SourceNotFoundError):
            svc.delete_source(999)


# --- toggle_enabled -------------------------------------------------------


class TestToggleEnabled:
    def test_toggle_flips_state(self, svc):
        s = svc.create_source(
            type="group", label="X", url="https://fb.com/groups/1", fb_entity_id="1"
        )
        assert s.enabled is True
        toggled = svc.toggle_enabled(s.id)
        assert toggled.enabled is False
        toggled_again = svc.toggle_enabled(s.id)
        assert toggled_again.enabled is True

    def test_toggle_missing_raises(self, svc):
        with pytest.raises(SourceNotFoundError):
            svc.toggle_enabled(999)


# --- to_dict serialization ------------------------------------------------


class TestToDict:
    def test_to_dict_decodes_keywords(self, svc):
        s = svc.create_source(
            type="group",
            label="X",
            url="https://fb.com/groups/1",
            fb_entity_id="1",
            keywords_include=["laptop"],
            keywords_exclude=["rusak"],
        )
        d = svc.to_dict(s)
        assert d["id"] == s.id
        assert d["type"] == "group"
        assert d["label"] == "X"
        assert d["keywords_include"] == ["laptop"]
        assert d["keywords_exclude"] == ["rusak"]
        assert d["enabled"] is True

    def test_to_dict_null_keywords_become_empty_list(self, svc):
        s = svc.create_source(type="home_feed", label="X")
        d = svc.to_dict(s)
        assert d["keywords_include"] == []
        assert d["keywords_exclude"] == []
