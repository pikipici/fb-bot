"""Router-level tests for /api/v1/fb-accounts.

Verifies:
* Admin-only guard on every endpoint (401/403 for unauthenticated / viewer).
* Full CRUD round-trip for an admin.
* Password is never returned in responses.
* Invalid purpose values are rejected.
* Reactivate / status filters behave.

Shares the ``client`` fixture pattern with ``tests/test_auth.py`` so each
test gets a fresh SQLite file, a fresh JWT secret, and the registered-
router list from the real ``server.main`` app.
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
    monkeypatch.setenv("CREDENTIALS_KEY", "WyzJqG3Vg9ZpUyFkq4bUxN9yxMG3xCyq4Rr8s3fL7dE=")
    monkeypatch.setenv("ENV", "development")
    auth_module._reset_jwt_secret_cache_for_tests()

    engine = create_engine(
        f"sqlite:///{tmp_path}/test_fb_accounts.db",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

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


def _register_and_login(client: TestClient, username: str, password: str,
                        role: str | None = None, admin_token: str | None = None) -> str:
    headers = {"Authorization": f"Bearer {admin_token}"} if admin_token else {}
    body = {"username": username, "password": password}
    if role:
        body["role"] = role
    client.post("/api/v1/auth/register", json=body, headers=headers)
    resp = client.post("/api/v1/auth/login",
                       json={"username": username, "password": password})
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


class TestAuthGuards:
    def test_unauthenticated_list_is_rejected(self, client):
        resp = client.get("/api/v1/fb-accounts")
        assert resp.status_code in (401, 403)

    def test_unauthenticated_create_is_rejected(self, client):
        resp = client.post(
            "/api/v1/fb-accounts",
            json={"label": "x", "email": "x@y", "password": "p"},
        )
        assert resp.status_code in (401, 403)

    def test_viewer_cannot_list(self, client, viewer_token):
        resp = client.get("/api/v1/fb-accounts", headers=_auth(viewer_token))
        assert resp.status_code == 403

    def test_viewer_cannot_create(self, client, viewer_token):
        resp = client.post(
            "/api/v1/fb-accounts",
            json={"label": "x", "email": "x@y", "password": "p"},
            headers=_auth(viewer_token),
        )
        assert resp.status_code == 403


class TestAdminCrud:
    def test_create_returns_account_without_password(self, client, admin_token):
        resp = client.post(
            "/api/v1/fb-accounts",
            json={
                "label": "Main",
                "email": "main@fb.test",
                "password": "supersecret",
                "purpose": "scrape",
                "notes": "primary scraper",
            },
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["label"] == "Main"
        assert body["email"] == "main@fb.test"
        assert body["purpose"] == "scrape"
        assert body["status"] == "ACTIVE"
        assert "password" not in body
        assert "password_encrypted" not in body

    def test_list_roundtrip(self, client, admin_token):
        client.post(
            "/api/v1/fb-accounts",
            json={"label": "A", "email": "a@fb.test", "password": "p1"},
            headers=_auth(admin_token),
        )
        client.post(
            "/api/v1/fb-accounts",
            json={"label": "B", "email": "b@fb.test", "password": "p2"},
            headers=_auth(admin_token),
        )
        resp = client.get("/api/v1/fb-accounts", headers=_auth(admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        labels = {a["label"] for a in body["accounts"]}
        assert labels == {"A", "B"}
        for account in body["accounts"]:
            assert "password" not in account
            assert "password_encrypted" not in account

    def test_update_label_and_password(self, client, admin_token):
        created = client.post(
            "/api/v1/fb-accounts",
            json={"label": "Old", "email": "o@fb.test", "password": "old"},
            headers=_auth(admin_token),
        ).json()
        resp = client.put(
            f"/api/v1/fb-accounts/{created['id']}",
            json={"label": "New", "password": "new-secret"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["label"] == "New"
        # Password must not leak through update either.
        assert "password" not in body

    def test_delete_removes_from_list(self, client, admin_token):
        created = client.post(
            "/api/v1/fb-accounts",
            json={"label": "Temp", "email": "t@fb.test", "password": "p"},
            headers=_auth(admin_token),
        ).json()
        resp = client.delete(
            f"/api/v1/fb-accounts/{created['id']}", headers=_auth(admin_token)
        )
        assert resp.status_code == 200
        listing = client.get("/api/v1/fb-accounts", headers=_auth(admin_token)).json()
        assert listing["total"] == 0

    def test_reactivate_resets_status(self, client, admin_token):
        created = client.post(
            "/api/v1/fb-accounts",
            json={"label": "Blocked", "email": "b@fb.test", "password": "p"},
            headers=_auth(admin_token),
        ).json()
        # Force status to BLOCKED via update
        client.put(
            f"/api/v1/fb-accounts/{created['id']}",
            json={"status": "BLOCKED"},
            headers=_auth(admin_token),
        )
        resp = client.post(
            f"/api/v1/fb-accounts/{created['id']}/reactivate",
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ACTIVE"


class TestValidation:
    def test_invalid_purpose_rejected(self, client, admin_token):
        resp = client.post(
            "/api/v1/fb-accounts",
            json={
                "label": "Bad",
                "email": "x@fb.test",
                "password": "p",
                "purpose": "delete-everything",
            },
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400

    def test_get_404_for_missing_id(self, client, admin_token):
        resp = client.get("/api/v1/fb-accounts/99999", headers=_auth(admin_token))
        assert resp.status_code == 404

    def test_update_404_for_missing_id(self, client, admin_token):
        resp = client.put(
            "/api/v1/fb-accounts/99999",
            json={"label": "x"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 404

    def test_delete_404_for_missing_id(self, client, admin_token):
        resp = client.delete(
            "/api/v1/fb-accounts/99999", headers=_auth(admin_token)
        )
        assert resp.status_code == 404
