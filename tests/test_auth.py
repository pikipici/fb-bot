"""Tests for auth endpoints."""

import pytest
from fastapi.testclient import TestClient

from server.database import Base, engine, SessionLocal
from server.main import app


@pytest.fixture(autouse=True)
def setup_db():
    """Create fresh database for each test."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def admin_token(client):
    """Register first user (auto-admin) and return token."""
    client.post("/api/v1/auth/register", json={
        "username": "admin",
        "password": "admin123",
    })
    resp = client.post("/api/v1/auth/login", json={
        "username": "admin",
        "password": "admin123",
    })
    return resp.json()["access_token"]


class TestRegister:
    def test_first_user_becomes_admin(self, client):
        resp = client.post("/api/v1/auth/register", json={
            "username": "firstuser",
            "password": "pass123",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["role"] == "admin"
        assert data["username"] == "firstuser"

    def test_duplicate_username_rejected(self, client):
        client.post("/api/v1/auth/register", json={
            "username": "admin",
            "password": "pass123",
        })
        resp = client.post("/api/v1/auth/register", json={
            "username": "admin",
            "password": "other123",
        })
        assert resp.status_code == 409


class TestLogin:
    def test_login_success(self, client):
        client.post("/api/v1/auth/register", json={
            "username": "admin",
            "password": "admin123",
        })
        resp = client.post("/api/v1/auth/login", json={
            "username": "admin",
            "password": "admin123",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    def test_login_wrong_password(self, client):
        client.post("/api/v1/auth/register", json={
            "username": "admin",
            "password": "admin123",
        })
        resp = client.post("/api/v1/auth/login", json={
            "username": "admin",
            "password": "wrong",
        })
        assert resp.status_code == 401

    def test_login_nonexistent_user(self, client):
        resp = client.post("/api/v1/auth/login", json={
            "username": "ghost",
            "password": "pass",
        })
        assert resp.status_code == 401


class TestRefresh:
    def test_refresh_token_works(self, client):
        client.post("/api/v1/auth/register", json={
            "username": "admin",
            "password": "admin123",
        })
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "admin",
            "password": "admin123",
        })
        refresh_token = login_resp.json()["refresh_token"]

        resp = client.post("/api/v1/auth/refresh", json={
            "refresh_token": refresh_token,
        })
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    def test_refresh_with_access_token_fails(self, client):
        client.post("/api/v1/auth/register", json={
            "username": "admin",
            "password": "admin123",
        })
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "admin",
            "password": "admin123",
        })
        access_token = login_resp.json()["access_token"]

        resp = client.post("/api/v1/auth/refresh", json={
            "refresh_token": access_token,
        })
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

    def test_get_me_unauthenticated(self, client):
        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 403


class TestRBAC:
    def test_viewer_cannot_approve(self, client, admin_token):
        # Register a viewer
        client.post(
            "/api/v1/auth/register",
            json={"username": "viewer1", "password": "pass123", "role": "viewer"},
        )
        # Login as viewer
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "viewer1",
            "password": "pass123",
        })
        viewer_token = login_resp.json()["access_token"]

        # Try to approve a draft
        resp = client.post(
            "/api/v1/approvals/1",
            json={"action": "approve"},
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert resp.status_code == 403

    def test_operator_can_approve(self, client, admin_token):
        # Register an operator
        client.post(
            "/api/v1/auth/register",
            json={"username": "operator1", "password": "pass123", "role": "operator"},
        )
        # Login as operator
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "operator1",
            "password": "pass123",
        })
        operator_token = login_resp.json()["access_token"]

        # Try to approve a draft
        resp = client.post(
            "/api/v1/approvals/1",
            json={"action": "approve"},
            headers={"Authorization": f"Bearer {operator_token}"},
        )
        # Should not be 403 (might be 404 since draft doesn't exist, but not forbidden)
        assert resp.status_code != 403
