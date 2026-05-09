"""Tests for auth endpoints.

Covers:
* First-user bootstrap → auto-admin.
* Subsequent ``/register`` requires a bearer token of a role=admin caller.
* Generic ``Invalid credentials`` error for login failure (no enumeration
  via distinct strings).
* JWT forgery is rejected (wrong signature / wrong algorithm).
* Refresh token path rejects access tokens and malformed ``sub`` claims.
"""

from __future__ import annotations

import os

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server import auth as auth_module
from server import database as database_module
from server.database import Base, get_db


@pytest.fixture
def client(tmp_path, monkeypatch):
    """FastAPI TestClient wired to a per-test SQLite file.

    Also forces ``server.auth._JWT_SECRET_CACHE`` to reset so each test
    starts with a clean secret resolution.
    """
    # Deterministic secret for signature-based assertions.
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-please-change")
    monkeypatch.setenv("ENV", "development")
    auth_module._reset_jwt_secret_cache_for_tests()

    engine = create_engine(
        f"sqlite:///{tmp_path}/test_auth.db",
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

    # Import here so ``get_db`` override wins even if ``server.main``
    # imported it first.
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


@pytest.fixture
def admin_token(client):
    """First user bootstraps as admin without auth."""
    client.post(
        "/api/v1/auth/register",
        json={"username": "admin", "password": "admin123"},
    )
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin123"},
    )
    return resp.json()["access_token"]


class TestRegisterBootstrap:
    def test_first_user_becomes_admin_without_auth(self, client):
        resp = client.post(
            "/api/v1/auth/register",
            json={"username": "firstuser", "password": "pass123"},
        )
        assert resp.status_code == 201
        assert resp.json()["role"] == "admin"

    def test_first_user_role_field_is_ignored(self, client):
        # Even if someone requests role=viewer, the first user is admin.
        resp = client.post(
            "/api/v1/auth/register",
            json={
                "username": "firstuser",
                "password": "pass123",
                "role": "viewer",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["role"] == "admin"


class TestRegisterRequiresAuthAfterBootstrap:
    def test_unauthenticated_register_is_rejected(self, client, admin_token):
        # After admin exists, anonymous registration is forbidden.
        resp = client.post(
            "/api/v1/auth/register",
            json={"username": "anon", "password": "pass123"},
        )
        assert resp.status_code == 401

    def test_non_admin_register_is_rejected(self, client, admin_token):
        # Create a viewer using the admin token, then try to register
        # a new user using that viewer's token.
        client.post(
            "/api/v1/auth/register",
            json={
                "username": "viewer1",
                "password": "pass123",
                "role": "viewer",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"username": "viewer1", "password": "pass123"},
        )
        viewer_token = login_resp.json()["access_token"]

        resp = client.post(
            "/api/v1/auth/register",
            json={"username": "escalated", "password": "p", "role": "admin"},
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert resp.status_code == 403

    def test_admin_can_register_new_admin(self, client, admin_token):
        resp = client.post(
            "/api/v1/auth/register",
            json={
                "username": "admin2",
                "password": "pass123",
                "role": "admin",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 201
        assert resp.json()["role"] == "admin"


class TestLogin:
    def test_login_success(self, client, admin_token):
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "admin123"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    def test_login_wrong_password_generic_error(self, client, admin_token):
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "wrong"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid credentials"

    def test_login_nonexistent_user_generic_error(self, client):
        # No prior user; just ensure we get 401 with the generic string.
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "ghost", "password": "pass"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid credentials"


class TestRefresh:
    def test_refresh_token_works(self, client, admin_token):
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "admin123"},
        )
        refresh_token = login.json()["refresh_token"]
        resp = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    def test_refresh_with_access_token_fails(self, client, admin_token):
        resp = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": admin_token},
        )
        assert resp.status_code == 401

    def test_refresh_with_non_numeric_sub_fails_cleanly(self, client):
        # Forge a refresh token with a non-numeric ``sub`` using the test secret.
        forged = jwt.encode(
            {"sub": "not-a-number", "type": "refresh"},
            os.environ["JWT_SECRET_KEY"],
            algorithm="HS256",
        )
        resp = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": forged},
        )
        # 401, never 500.
        assert resp.status_code == 401


class TestJwtForgery:
    def test_token_signed_with_wrong_secret_is_rejected(self, client, admin_token):
        forged = jwt.encode(
            {"sub": "1", "role": "admin", "type": "access"},
            "attacker-secret",
            algorithm="HS256",
        )
        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {forged}"},
        )
        assert resp.status_code == 401

    def test_token_with_none_algorithm_is_rejected(self, client, admin_token):
        # Classic "alg: none" attempt. PyJWT rejects this by default.
        forged = jwt.encode(
            {"sub": "1", "role": "admin", "type": "access"},
            key="",
            algorithm="none",
        )
        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {forged}"},
        )
        assert resp.status_code == 401


class TestMe:
    def test_get_me_authenticated(self, client, admin_token):
        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "admin"
        assert data["role"] == "admin"

    def test_get_me_unauthenticated_is_denied(self, client):
        resp = client.get("/api/v1/auth/me")
        # HTTPBearer default returns 403 when Authorization is missing.
        # Any 4xx is acceptable here; do not accept 200.
        assert resp.status_code in (401, 403)


class TestRBAC:
    def test_viewer_cannot_approve(self, client, admin_token):
        client.post(
            "/api/v1/auth/register",
            json={
                "username": "viewer1",
                "password": "pass123",
                "role": "viewer",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "viewer1", "password": "pass123"},
        )
        viewer_token = login.json()["access_token"]

        resp = client.post(
            "/api/v1/approvals/1",
            json={"action": "approve"},
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert resp.status_code == 403

    def test_operator_can_approve(self, client, admin_token):
        client.post(
            "/api/v1/auth/register",
            json={
                "username": "operator1",
                "password": "pass123",
                "role": "operator",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "operator1", "password": "pass123"},
        )
        operator_token = login.json()["access_token"]

        resp = client.post(
            "/api/v1/approvals/1",
            json={"action": "approve"},
            headers={"Authorization": f"Bearer {operator_token}"},
        )
        # Draft does not exist, so 404 is acceptable. Never 403.
        assert resp.status_code != 403


class TestProductionFailFast:
    def test_missing_jwt_secret_in_production_raises(self, monkeypatch):
        """``_resolve_jwt_secret`` must fail-closed in production."""
        monkeypatch.setenv("ENV", "production")
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        auth_module._reset_jwt_secret_cache_for_tests()
        with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
            auth_module._resolve_jwt_secret()
        auth_module._reset_jwt_secret_cache_for_tests()

    def test_placeholder_jwt_secret_in_production_raises(self, monkeypatch):
        monkeypatch.setenv("ENV", "production")
        monkeypatch.setenv("JWT_SECRET_KEY", "change-me")
        auth_module._reset_jwt_secret_cache_for_tests()
        with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
            auth_module._resolve_jwt_secret()
        auth_module._reset_jwt_secret_cache_for_tests()
