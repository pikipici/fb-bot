"""Router-level tests for /api/v1/sources.

Shares the ``client`` fixture pattern with other router tests — each test
gets a fresh SQLite DB and the registered-router list from real
``server.main``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server import auth as auth_module
from server import database as database_module
from server.database import Base, get_db


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-please-change")
    monkeypatch.setenv(
        "CREDENTIALS_KEY", "WyzJqG3Vg9ZpUyFkq4bUxN9yxMG3xCyq4Rr8s3fL7dE="
    )
    monkeypatch.setenv("ENV", "development")
    auth_module._reset_jwt_secret_cache_for_tests()

    engine = create_engine(
        f"sqlite:///{tmp_path}/test_sources.db",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=engine
    )

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    from server.main import app

    app.dependency_overrides[get_db] = override_get_db
    original_session_local = database_module.SessionLocal
    database_module.SessionLocal = TestingSessionLocal
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_db, None)
        database_module.SessionLocal = original_session_local
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        auth_module._reset_jwt_secret_cache_for_tests()


def _register_and_login(
    client: TestClient,
    username: str,
    password: str,
    role: str | None = None,
    admin_token: str | None = None,
) -> str:
    headers = (
        {"Authorization": f"Bearer {admin_token}"} if admin_token else {}
    )
    body = {"username": username, "password": password}
    if role:
        body["role"] = role
    client.post("/api/v1/auth/register", json=body, headers=headers)
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture
def admin_token(client):
    return _register_and_login(client, "admin", "admin123")


@pytest.fixture
def viewer_token(client, admin_token):
    return _register_and_login(
        client, "viewer", "viewer123", role="viewer", admin_token=admin_token
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- auth guards ---------------------------------------------------------


class TestAuthGuards:
    def test_unauthenticated_list_rejected(self, client):
        resp = client.get("/api/v1/sources")
        assert resp.status_code in (401, 403)

    def test_unauthenticated_create_rejected(self, client):
        resp = client.post(
            "/api/v1/sources",
            json={"type": "home_feed", "label": "x"},
        )
        assert resp.status_code in (401, 403)

    def test_viewer_cannot_list(self, client, viewer_token):
        resp = client.get("/api/v1/sources", headers=_auth(viewer_token))
        assert resp.status_code == 403

    def test_viewer_cannot_create(self, client, viewer_token):
        resp = client.post(
            "/api/v1/sources",
            json={"type": "home_feed", "label": "x"},
            headers=_auth(viewer_token),
        )
        assert resp.status_code == 403


# --- create ---------------------------------------------------------------


class TestCreateSource:
    def test_create_home_feed(self, client, admin_token):
        resp = client.post(
            "/api/v1/sources",
            json={"type": "home_feed", "label": "Beranda"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()["source"]
        assert data["id"] is not None
        assert data["type"] == "home_feed"
        assert data["label"] == "Beranda"
        assert data["enabled"] is True
        assert data["keywords_include"] == []
        assert data["keywords_exclude"] == []

    def test_create_group_with_keywords(self, client, admin_token):
        resp = client.post(
            "/api/v1/sources",
            json={
                "type": "group",
                "label": "Laptop Bekas",
                "url": "https://www.facebook.com/groups/12345",
                "fb_entity_id": "12345",
                "keywords_include": ["laptop", "Gaming"],
                "keywords_exclude": ["rusak"],
            },
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()["source"]
        assert data["type"] == "group"
        assert data["keywords_include"] == ["laptop", "gaming"]
        assert data["keywords_exclude"] == ["rusak"]

    def test_create_invalid_type_returns_400(self, client, admin_token):
        resp = client.post(
            "/api/v1/sources",
            json={"type": "profile", "label": "x"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400

    def test_create_duplicate_home_feed_returns_409(self, client, admin_token):
        client.post(
            "/api/v1/sources",
            json={"type": "home_feed", "label": "B1"},
            headers=_auth(admin_token),
        )
        resp = client.post(
            "/api/v1/sources",
            json={"type": "home_feed", "label": "B2"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 409

    def test_create_missing_label_returns_422(self, client, admin_token):
        resp = client.post(
            "/api/v1/sources",
            json={"type": "home_feed"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 422


# --- list -----------------------------------------------------------------


class TestListSources:
    def test_list_empty(self, client, admin_token):
        resp = client.get("/api/v1/sources", headers=_auth(admin_token))
        assert resp.status_code == 200
        assert resp.json() == {"sources": [], "total": 0}

    def test_list_returns_all_sources(self, client, admin_token):
        client.post(
            "/api/v1/sources",
            json={"type": "home_feed", "label": "B"},
            headers=_auth(admin_token),
        )
        client.post(
            "/api/v1/sources",
            json={
                "type": "group",
                "label": "G1",
                "url": "https://fb.com/groups/1",
                "fb_entity_id": "1",
            },
            headers=_auth(admin_token),
        )
        resp = client.get("/api/v1/sources", headers=_auth(admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert len(body["sources"]) == 2


# --- update ---------------------------------------------------------------


class TestUpdateSource:
    def test_patch_label(self, client, admin_token):
        created = client.post(
            "/api/v1/sources",
            json={
                "type": "group",
                "label": "Old",
                "url": "https://fb.com/groups/1",
                "fb_entity_id": "1",
            },
            headers=_auth(admin_token),
        ).json()["source"]
        resp = client.patch(
            f"/api/v1/sources/{created['id']}",
            json={"label": "New Label"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        assert resp.json()["source"]["label"] == "New Label"

    def test_patch_keywords_replaces(self, client, admin_token):
        created = client.post(
            "/api/v1/sources",
            json={
                "type": "group",
                "label": "X",
                "url": "https://fb.com/groups/1",
                "fb_entity_id": "1",
                "keywords_include": ["old"],
            },
            headers=_auth(admin_token),
        ).json()["source"]
        resp = client.patch(
            f"/api/v1/sources/{created['id']}",
            json={"keywords_include": ["new1", "new2"]},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        assert resp.json()["source"]["keywords_include"] == ["new1", "new2"]

    def test_patch_missing_returns_404(self, client, admin_token):
        resp = client.patch(
            "/api/v1/sources/999",
            json={"label": "x"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 404


# --- toggle ---------------------------------------------------------------


class TestToggleEnabled:
    def test_toggle_flips(self, client, admin_token):
        created = client.post(
            "/api/v1/sources",
            json={"type": "home_feed", "label": "B"},
            headers=_auth(admin_token),
        ).json()["source"]
        resp1 = client.post(
            f"/api/v1/sources/{created['id']}/toggle",
            headers=_auth(admin_token),
        )
        assert resp1.status_code == 200
        assert resp1.json()["source"]["enabled"] is False
        resp2 = client.post(
            f"/api/v1/sources/{created['id']}/toggle",
            headers=_auth(admin_token),
        )
        assert resp2.json()["source"]["enabled"] is True

    def test_toggle_missing_returns_404(self, client, admin_token):
        resp = client.post(
            "/api/v1/sources/999/toggle",
            headers=_auth(admin_token),
        )
        assert resp.status_code == 404


# --- delete ---------------------------------------------------------------


class TestDeleteSource:
    def test_delete_removes_row(self, client, admin_token):
        created = client.post(
            "/api/v1/sources",
            json={"type": "home_feed", "label": "B"},
            headers=_auth(admin_token),
        ).json()["source"]
        resp = client.delete(
            f"/api/v1/sources/{created['id']}",
            headers=_auth(admin_token),
        )
        assert resp.status_code == 204
        # ensure gone
        listed = client.get(
            "/api/v1/sources", headers=_auth(admin_token)
        ).json()
        assert listed["total"] == 0

    def test_delete_missing_returns_404(self, client, admin_token):
        resp = client.delete(
            "/api/v1/sources/999",
            headers=_auth(admin_token),
        )
        assert resp.status_code == 404
