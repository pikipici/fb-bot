"""Router tests for /api/v1/rate-limit/status — quota snapshot endpoint."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server import auth as auth_module
from server import database as database_module
from server.database import Base, get_db
from server.models import CommentHistory, Source, TrendingPost


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-please-change")
    monkeypatch.setenv(
        "CREDENTIALS_KEY", "WyzJqG3Vg9ZpUyFkq4bUxN9yxMG3xCyq4Rr8s3fL7dE="
    )
    monkeypatch.setenv("ENV", "development")
    auth_module._reset_jwt_secret_cache_for_tests()

    engine = create_engine(
        f"sqlite:///{tmp_path}/test_ratelimit_router.db",
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
            test_client._session_local = TestingSessionLocal
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


def _seed_history(
    client: TestClient, *, count: int, minutes_ago: int, status: str = "SENT"
):
    """Seed one Source + TrendingPost + N CommentHistory rows in test DB."""
    session = client._session_local()
    try:
        source = (
            session.query(Source)
            .filter(Source.label == "beranda")
            .one_or_none()
        )
        if source is None:
            source = Source(
                type="home_feed",
                label="beranda",
                url="https://www.facebook.com/home.php",
                enabled=True,
            )
            session.add(source)
            session.commit()
        post = TrendingPost(
            fb_post_id=f"pfbid_test_{minutes_ago}_{status}",
            source_id=source.id,
            author_name="Test",
            text_snippet="dummy",
            post_url="https://www.facebook.com/t/1",
            status="DRAFTED",
        )
        session.add(post)
        session.commit()
        base = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        for i in range(count):
            session.add(
                CommentHistory(
                    trending_post_id=post.id,
                    comment_text="x",
                    status=status,
                    sent_at=base + timedelta(seconds=i),
                )
            )
        session.commit()
    finally:
        session.close()


# --- auth guards ---------------------------------------------------------


class TestAuthGuards:
    def test_unauth_rejected(self, client):
        resp = client.get("/api/v1/rate-limit/status")
        assert resp.status_code in (401, 403)

    def test_viewer_allowed(self, client, viewer_token):
        resp = client.get(
            "/api/v1/rate-limit/status", headers=_auth(viewer_token)
        )
        assert resp.status_code == 200


# --- shape ---------------------------------------------------------------


class TestQuotaShape:
    def test_default_empty_shape(self, client, admin_token):
        resp = client.get(
            "/api/v1/rate-limit/status", headers=_auth(admin_token)
        )
        assert resp.status_code == 200
        body = resp.json()
        q = body["quota"]
        assert q["allowed"] is True
        assert q["used"] == 0
        assert q["remaining"] == 5
        assert q["limit"] == 5
        assert q["window_hours"] == 6
        assert q["resets_at"] is None

    def test_counts_sent_within_window(self, client, admin_token):
        _seed_history(client, count=3, minutes_ago=30)
        resp = client.get(
            "/api/v1/rate-limit/status", headers=_auth(admin_token)
        )
        q = resp.json()["quota"]
        assert q["used"] == 3
        assert q["remaining"] == 2
        assert q["allowed"] is True
        assert q["resets_at"] is not None

    def test_blocked_when_full(self, client, admin_token):
        _seed_history(client, count=5, minutes_ago=10)
        resp = client.get(
            "/api/v1/rate-limit/status", headers=_auth(admin_token)
        )
        q = resp.json()["quota"]
        assert q["used"] == 5
        assert q["remaining"] == 0
        assert q["allowed"] is False

    def test_ignores_old_sends(self, client, admin_token):
        _seed_history(client, count=5, minutes_ago=60 * 7)
        resp = client.get(
            "/api/v1/rate-limit/status", headers=_auth(admin_token)
        )
        q = resp.json()["quota"]
        assert q["used"] == 0
        assert q["allowed"] is True

    def test_ignores_failed_status(self, client, admin_token):
        _seed_history(client, count=5, minutes_ago=10, status="FAILED")
        resp = client.get(
            "/api/v1/rate-limit/status", headers=_auth(admin_token)
        )
        q = resp.json()["quota"]
        assert q["used"] == 0
        assert q["allowed"] is True
