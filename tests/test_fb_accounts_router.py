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
        resp = client.get("/api/v1/fb-accounts", headers=_auth(admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        labels = {a["label"] for a in body["accounts"]}
        assert labels == {"A"}
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


class TestSingleAccountLimit:
    """FB Bot is single-account by design — reject POST when one already exists."""

    def test_second_create_returns_409(self, client, admin_token):
        first = client.post(
            "/api/v1/fb-accounts",
            json={"label": "Primary", "email": "p@fb.test", "password": "p1"},
            headers=_auth(admin_token),
        )
        assert first.status_code == 201

        second = client.post(
            "/api/v1/fb-accounts",
            json={"label": "Another", "email": "a@fb.test", "password": "p2"},
            headers=_auth(admin_token),
        )
        assert second.status_code == 409
        assert "already exists" in second.json()["detail"].lower()

    def test_create_allowed_again_after_delete(self, client, admin_token):
        created = client.post(
            "/api/v1/fb-accounts",
            json={"label": "First", "email": "f@fb.test", "password": "p1"},
            headers=_auth(admin_token),
        ).json()
        client.delete(
            f"/api/v1/fb-accounts/{created['id']}", headers=_auth(admin_token)
        )
        resp = client.post(
            "/api/v1/fb-accounts",
            json={"label": "Second", "email": "s@fb.test", "password": "p2"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201
        assert resp.json()["label"] == "Second"

    def test_blocked_account_still_blocks_second_create(self, client, admin_token):
        created = client.post(
            "/api/v1/fb-accounts",
            json={"label": "Primary", "email": "p@fb.test", "password": "p1"},
            headers=_auth(admin_token),
        ).json()
        client.put(
            f"/api/v1/fb-accounts/{created['id']}",
            json={"status": "BLOCKED"},
            headers=_auth(admin_token),
        )
        resp = client.post(
            "/api/v1/fb-accounts",
            json={"label": "Replacement", "email": "r@fb.test", "password": "p2"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 409


class TestGetCurrentAccount:
    """/fb-accounts/current returns the single managed account (or null)."""

    def test_returns_null_when_empty(self, client, admin_token):
        resp = client.get("/api/v1/fb-accounts/current", headers=_auth(admin_token))
        assert resp.status_code == 200
        assert resp.json() == {"account": None}

    def test_returns_account_when_exists(self, client, admin_token):
        client.post(
            "/api/v1/fb-accounts",
            json={"label": "Main", "email": "m@fb.test", "password": "p"},
            headers=_auth(admin_token),
        )
        resp = client.get("/api/v1/fb-accounts/current", headers=_auth(admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["account"] is not None
        assert body["account"]["label"] == "Main"
        assert body["account"]["email"] == "m@fb.test"
        assert "password" not in body["account"]

    def test_viewer_cannot_read_current(self, client, viewer_token):
        resp = client.get(
            "/api/v1/fb-accounts/current", headers=_auth(viewer_token)
        )
        assert resp.status_code == 403

    def test_returns_disabled_account(self, client, admin_token):
        """Current endpoint includes disabled accounts (canonical single read)."""
        created = client.post(
            "/api/v1/fb-accounts",
            json={"label": "Main", "email": "m@fb.test", "password": "p"},
            headers=_auth(admin_token),
        ).json()
        client.put(
            f"/api/v1/fb-accounts/{created['id']}",
            json={"status": "DISABLED"},
            headers=_auth(admin_token),
        )
        resp = client.get(
            "/api/v1/fb-accounts/current", headers=_auth(admin_token)
        )
        body = resp.json()
        assert body["account"] is not None
        assert body["account"]["status"] == "DISABLED"


class TestCookiePreview:
    """POST /fb-accounts/preview-cookie validates & returns profile info
    WITHOUT saving anything to the DB.
    """

    def test_preview_happy_path(self, client, admin_token, monkeypatch):
        from server.services import cookie_session_service as css

        async def fake_validate(_cookies):
            return css.ProfileInfo(
                fb_user_id="100001",
                name="Test User",
                profile_pic_url="https://fb.test/pic.jpg",
            )

        monkeypatch.setattr(
            "server.routers.fb_accounts.validate_and_fetch_profile",
            fake_validate,
        )

        resp = client.post(
            "/api/v1/fb-accounts/preview-cookie",
            json={"raw_cookies": "c_user=100001; xs=abc"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["preview"]["fb_user_id"] == "100001"
        assert body["preview"]["name"] == "Test User"
        assert body["preview"]["profile_pic_url"] == "https://fb.test/pic.jpg"

    def test_preview_invalid_cookie_returns_400(self, client, admin_token, monkeypatch):
        from server.services.cookie_session_service import (
            CookieValidationError,
        )

        async def fake_validate(_cookies):
            raise CookieValidationError("Cookie expired")

        monkeypatch.setattr(
            "server.routers.fb_accounts.validate_and_fetch_profile",
            fake_validate,
        )

        resp = client.post(
            "/api/v1/fb-accounts/preview-cookie",
            json={"raw_cookies": "c_user=bad; xs=bad"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400
        assert "Cookie expired" in resp.json()["detail"]

    def test_preview_empty_raw_returns_400(self, client, admin_token):
        resp = client.post(
            "/api/v1/fb-accounts/preview-cookie",
            json={"raw_cookies": ""},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400

    def test_preview_does_not_save(self, client, admin_token, monkeypatch):
        """Preview must leave the DB untouched even on success."""
        from server.services import cookie_session_service as css

        async def fake_validate(_cookies):
            return css.ProfileInfo("100001", "Test User", None)

        monkeypatch.setattr(
            "server.routers.fb_accounts.validate_and_fetch_profile",
            fake_validate,
        )

        client.post(
            "/api/v1/fb-accounts/preview-cookie",
            json={"raw_cookies": "c_user=100001; xs=abc"},
            headers=_auth(admin_token),
        )
        current = client.get(
            "/api/v1/fb-accounts/current", headers=_auth(admin_token)
        ).json()
        assert current["account"] is None

    def test_viewer_cannot_preview(self, client, viewer_token):
        resp = client.post(
            "/api/v1/fb-accounts/preview-cookie",
            json={"raw_cookies": "c_user=1"},
            headers=_auth(viewer_token),
        )
        assert resp.status_code == 403


class TestCookieConnect:
    """POST /fb-accounts/connect-cookie validates cookie, encrypts, saves."""

    def test_connect_happy_path(self, client, admin_token, monkeypatch):
        from server.services import cookie_session_service as css

        async def fake_validate(_cookies):
            return css.ProfileInfo(
                fb_user_id="100001",
                name="Test User",
                profile_pic_url="https://fb.test/pic.jpg",
            )

        monkeypatch.setattr(
            "server.routers.fb_accounts.validate_and_fetch_profile",
            fake_validate,
        )

        resp = client.post(
            "/api/v1/fb-accounts/connect-cookie",
            json={
                "label": "Main FB",
                "raw_cookies": "c_user=100001; xs=abc; datr=xyz",
            },
            headers=_auth(admin_token),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["label"] == "Main FB"
        assert body["fb_user_id"] == "100001"
        assert body["fb_name"] == "Test User"
        assert body["fb_profile_pic_url"] == "https://fb.test/pic.jpg"
        # Secrets must never be returned.
        assert "raw_cookies" not in body
        assert "cookies_encrypted" not in body
        assert "password" not in body

        # Verify the account is now persisted.
        current = client.get(
            "/api/v1/fb-accounts/current", headers=_auth(admin_token)
        ).json()
        assert current["account"]["fb_user_id"] == "100001"

    def test_connect_rejects_when_account_already_exists(
        self, client, admin_token, monkeypatch
    ):
        from server.services import cookie_session_service as css

        async def fake_validate(_cookies):
            return css.ProfileInfo("100001", "Test", None)

        monkeypatch.setattr(
            "server.routers.fb_accounts.validate_and_fetch_profile",
            fake_validate,
        )
        client.post(
            "/api/v1/fb-accounts",
            json={"label": "Old", "email": "e@fb", "password": "p"},
            headers=_auth(admin_token),
        )
        resp = client.post(
            "/api/v1/fb-accounts/connect-cookie",
            json={"label": "New", "raw_cookies": "c_user=100001; xs=abc"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 409

    def test_connect_invalid_cookie_returns_400(
        self, client, admin_token, monkeypatch
    ):
        from server.services.cookie_session_service import (
            CookieValidationError,
        )

        async def fake_validate(_cookies):
            raise CookieValidationError("Cookie gak valid")

        monkeypatch.setattr(
            "server.routers.fb_accounts.validate_and_fetch_profile",
            fake_validate,
        )

        resp = client.post(
            "/api/v1/fb-accounts/connect-cookie",
            json={"label": "X", "raw_cookies": "c_user=bad"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400

    def test_connect_empty_label_rejected(self, client, admin_token):
        resp = client.post(
            "/api/v1/fb-accounts/connect-cookie",
            json={"label": "", "raw_cookies": "c_user=1"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 400

    def test_connect_persists_encrypted_cookies_and_sets_status_active(
        self, client, admin_token, monkeypatch
    ):
        from server.services import cookie_session_service as css

        async def fake_validate(_cookies):
            return css.ProfileInfo("100001", "Test", None)

        monkeypatch.setattr(
            "server.routers.fb_accounts.validate_and_fetch_profile",
            fake_validate,
        )

        client.post(
            "/api/v1/fb-accounts/connect-cookie",
            json={"label": "Main", "raw_cookies": "c_user=100001; xs=abc"},
            headers=_auth(admin_token),
        )
        # Fetch via raw DB to confirm cookies_encrypted is populated &
        # non-empty (we never expose it via API).
        from server import database as database_module
        from server.models import FBAccount

        with database_module.SessionLocal() as db:
            account = db.query(FBAccount).first()
            assert account is not None
            assert account.cookies_encrypted
            assert "c_user" not in account.cookies_encrypted  # encrypted
            assert account.status == "ACTIVE"
            assert account.fb_user_id == "100001"

    def test_viewer_cannot_connect(self, client, viewer_token):
        resp = client.post(
            "/api/v1/fb-accounts/connect-cookie",
            json={"label": "X", "raw_cookies": "c_user=1"},
            headers=_auth(viewer_token),
        )
        assert resp.status_code == 403
