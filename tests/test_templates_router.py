"""Router tests for /api/v1/template — single active comment template."""
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
        f"sqlite:///{tmp_path}/test_templates.db",
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
    def test_unauth_get_rejected(self, client):
        resp = client.get("/api/v1/template")
        assert resp.status_code in (401, 403)

    def test_unauth_put_rejected(self, client):
        resp = client.put(
            "/api/v1/template", json={"template_text": "halo"}
        )
        assert resp.status_code in (401, 403)

    def test_viewer_can_read(self, client, viewer_token):
        resp = client.get(
            "/api/v1/template", headers=_auth(viewer_token)
        )
        assert resp.status_code == 200

    def test_viewer_cannot_write(self, client, viewer_token):
        resp = client.put(
            "/api/v1/template",
            json={"template_text": "halo"},
            headers=_auth(viewer_token),
        )
        assert resp.status_code == 403


# --- GET -----------------------------------------------------------------


class TestGetTemplate:
    def test_returns_null_when_empty(self, client, admin_token):
        resp = client.get("/api/v1/template", headers=_auth(admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["template"] is None

    def test_returns_active_after_upsert(self, client, admin_token):
        client.put(
            "/api/v1/template",
            json={"template_text": "Halo {author_name}!"},
            headers=_auth(admin_token),
        )
        resp = client.get("/api/v1/template", headers=_auth(admin_token))
        assert resp.status_code == 200
        tpl = resp.json()["template"]
        assert tpl is not None
        assert tpl["template_text"] == "Halo {author_name}!"
        assert tpl["is_active"] is True
        assert tpl["id"] is not None


# --- PUT -----------------------------------------------------------------


class TestPutTemplate:
    def test_creates_first_template(self, client, admin_token):
        resp = client.put(
            "/api/v1/template",
            json={"template_text": "Mantap {author_name}"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200, resp.text
        tpl = resp.json()["template"]
        assert tpl["template_text"] == "Mantap {author_name}"

    def test_updates_existing_template(self, client, admin_token):
        r1 = client.put(
            "/api/v1/template",
            json={"template_text": "v1"},
            headers=_auth(admin_token),
        )
        id1 = r1.json()["template"]["id"]

        r2 = client.put(
            "/api/v1/template",
            json={"template_text": "v2 kenceng"},
            headers=_auth(admin_token),
        )
        tpl = r2.json()["template"]
        assert tpl["id"] == id1
        assert tpl["template_text"] == "v2 kenceng"

    def test_rejects_empty_text(self, client, admin_token):
        resp = client.put(
            "/api/v1/template",
            json={"template_text": "   "},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400

    def test_rejects_missing_field(self, client, admin_token):
        resp = client.put(
            "/api/v1/template", json={}, headers=_auth(admin_token)
        )
        assert resp.status_code == 422
