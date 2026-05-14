"""Router tests for /api/v1/comment-activity/today — informational counter."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server import auth as auth_module
from server import database as database_module
from server.database import Base, get_db
from server.models import CommentHistory, Source, TrendingPost

WIB = ZoneInfo("Asia/Jakarta")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-please-change")
    monkeypatch.setenv(
        "CREDENTIALS_KEY", "WyzJqG3Vg9ZpUyFkq4bUxN9yxMG3xCyq4Rr8s3fL7dE="
    )
    monkeypatch.setenv("ENV", "development")
    auth_module._reset_jwt_secret_cache_for_tests()

    engine = create_engine(
        f"sqlite:///{tmp_path}/test_activity_router.db",
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


def _seed_rows(
    client: TestClient,
    *,
    count: int,
    sent_at: datetime,
    status: str = "SENT",
):
    """Seed Source + TrendingPost + N CommentHistory rows at ``sent_at``."""
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
            fb_post_id=f"pfbid_{sent_at.isoformat()}_{status}",
            source_id=source.id,
            author_name="Test",
            text_snippet="dummy",
            post_url="https://www.facebook.com/t/1",
            status="DRAFTED",
        )
        session.add(post)
        session.commit()
        for i in range(count):
            session.add(
                CommentHistory(
                    trending_post_id=post.id,
                    comment_text="x",
                    status=status,
                    sent_at=sent_at + timedelta(seconds=i),
                )
            )
        session.commit()
    finally:
        session.close()


# --- auth guards ---------------------------------------------------------


class TestAuthGuards:
    def test_unauth_rejected(self, client):
        resp = client.get("/api/v1/comment-activity/today")
        assert resp.status_code in (401, 403)

    def test_viewer_allowed(self, client, viewer_token):
        resp = client.get(
            "/api/v1/comment-activity/today", headers=_auth(viewer_token)
        )
        assert resp.status_code == 200


# --- shape ---------------------------------------------------------------


class TestActivityShape:
    def test_empty_shape(self, client, admin_token):
        resp = client.get(
            "/api/v1/comment-activity/today", headers=_auth(admin_token)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count_today"] == 0
        assert body["tz"] == "Asia/Jakarta"
        assert body["date"] == datetime.now(WIB).date().isoformat()

    def test_counts_today_sent(self, client, admin_token):
        # Noon WIB today — comfortably inside the window.
        noon_wib = datetime.now(WIB).replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        if noon_wib > datetime.now(WIB):
            noon_wib = datetime.now(WIB) - timedelta(minutes=5)
        _seed_rows(
            client,
            count=3,
            sent_at=noon_wib.astimezone(timezone.utc),
        )

        resp = client.get(
            "/api/v1/comment-activity/today", headers=_auth(admin_token)
        )
        assert resp.json()["count_today"] == 3

    def test_ignores_yesterday_wib(self, client, admin_token):
        # 23:30 WIB yesterday — before today's 00:00 WIB boundary.
        yest = (
            datetime.now(WIB).replace(hour=23, minute=30, second=0, microsecond=0)
            - timedelta(days=1)
        )
        _seed_rows(client, count=5, sent_at=yest.astimezone(timezone.utc))

        resp = client.get(
            "/api/v1/comment-activity/today", headers=_auth(admin_token)
        )
        assert resp.json()["count_today"] == 0

    def test_ignores_failed_status(self, client, admin_token):
        noon_wib = datetime.now(WIB).replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        if noon_wib > datetime.now(WIB):
            noon_wib = datetime.now(WIB) - timedelta(minutes=5)
        _seed_rows(
            client,
            count=4,
            sent_at=noon_wib.astimezone(timezone.utc),
            status="FAILED",
        )

        resp = client.get(
            "/api/v1/comment-activity/today", headers=_auth(admin_token)
        )
        assert resp.json()["count_today"] == 0
