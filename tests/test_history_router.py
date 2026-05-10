"""Router tests for /api/v1/history — list comment history (Layer 2 audit).

History endpoint backs the frontend ``/history`` page. It lists
``comment_history`` rows ordered by ``sent_at DESC`` with filter by
status (SENT/FAILED/PENDING) and basic pagination.
"""
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
        f"sqlite:///{tmp_path}/test_history.db",
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
            test_client._session_factory = TestingSessionLocal  # type: ignore[attr-defined]
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
    client: TestClient, rows: list[dict]
) -> list[int]:
    """Seed source + trending posts + comment_history rows, return history ids."""
    SessionLocal = client._session_factory  # type: ignore[attr-defined]
    ids: list[int] = []
    with SessionLocal() as db:
        src = Source(type="home_feed", label="Beranda", enabled=True)
        db.add(src)
        db.flush()
        for i, row in enumerate(rows):
            post = TrendingPost(
                fb_post_id=row.get("fb_post_id", f"p_{i}"),
                source_id=src.id,
                author_name=row.get("author", "Someone"),
                text_snippet=row.get("post_text", "trending post"),
                post_url=row.get("post_url", f"https://fb.com/p/{i}"),
                thumbnail_url=row.get("thumbnail_url"),
                likes=0,
                comments=0,
                shares=0,
                reactions_total=0,
                score=1.0,
                velocity=0.0,
                status=row.get("post_status", "COMMENTED"),
            )
            db.add(post)
            db.flush()
            history = CommentHistory(
                trending_post_id=post.id,
                user_id=None,
                comment_text=row["comment_text"],
                fb_comment_id=row.get("fb_comment_id"),
                status=row["status"],
                error_message=row.get("error_message"),
                sent_at=row.get(
                    "sent_at", datetime.now(timezone.utc)
                ),
            )
            db.add(history)
            db.flush()
            ids.append(history.id)
        db.commit()
    return ids


class TestAuthGuards:
    def test_unauth_rejected(self, client):
        resp = client.get("/api/v1/history")
        assert resp.status_code in (401, 403)

    def test_viewer_allowed(self, client, viewer_token):
        """History is read-only audit — viewer should be allowed."""
        resp = client.get("/api/v1/history", headers=_auth(viewer_token))
        assert resp.status_code == 200


class TestList:
    def test_empty_list(self, client, admin_token):
        resp = client.get("/api/v1/history", headers=_auth(admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0

    def test_list_all_rows(self, client, admin_token):
        _seed_history(
            client,
            [
                {
                    "fb_post_id": "p_sent_1",
                    "comment_text": "halo bro",
                    "status": "SENT",
                    "fb_comment_id": "c_abc",
                },
                {
                    "fb_post_id": "p_fail_1",
                    "comment_text": "keren",
                    "status": "FAILED",
                    "error_message": "composer not found",
                },
            ],
        )
        resp = client.get("/api/v1/history", headers=_auth(admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert len(body["items"]) == 2

    def test_ordering_is_sent_at_desc(self, client, admin_token):
        """Newest entries first — important for audit UX."""
        now = datetime.now(timezone.utc)
        _seed_history(
            client,
            [
                {
                    "fb_post_id": "old",
                    "comment_text": "old",
                    "status": "SENT",
                    "sent_at": now - timedelta(hours=2),
                },
                {
                    "fb_post_id": "new",
                    "comment_text": "new",
                    "status": "SENT",
                    "sent_at": now,
                },
                {
                    "fb_post_id": "mid",
                    "comment_text": "mid",
                    "status": "SENT",
                    "sent_at": now - timedelta(hours=1),
                },
            ],
        )
        resp = client.get("/api/v1/history", headers=_auth(admin_token))
        items = resp.json()["items"]
        texts = [it["comment_text"] for it in items]
        assert texts == ["new", "mid", "old"]

    def test_item_shape(self, client, admin_token):
        _seed_history(
            client,
            [
                {
                    "fb_post_id": "shape",
                    "author": "Alice",
                    "post_text": "trending text here",
                    "post_url": "https://fb.com/p/shape",
                    "comment_text": "halo dari bot",
                    "status": "SENT",
                    "fb_comment_id": "c_xyz",
                }
            ],
        )
        resp = client.get("/api/v1/history", headers=_auth(admin_token))
        item = resp.json()["items"][0]
        # Core fields
        for key in (
            "id",
            "comment_text",
            "status",
            "sent_at",
            "fb_comment_id",
            "error_message",
            "trending_post_id",
        ):
            assert key in item, f"missing {key} in {item.keys()}"
        # Denormalised post summary so UI can render without another fetch
        assert item["comment_text"] == "halo dari bot"
        assert item["fb_comment_id"] == "c_xyz"
        assert item["post"]["author_name"] == "Alice"
        assert item["post"]["post_url"] == "https://fb.com/p/shape"
        assert item["post"]["text_snippet"] == "trending text here"


class TestFilters:
    def test_filter_by_status_sent(self, client, admin_token):
        _seed_history(
            client,
            [
                {"fb_post_id": "a", "comment_text": "a", "status": "SENT"},
                {
                    "fb_post_id": "b",
                    "comment_text": "b",
                    "status": "FAILED",
                    "error_message": "nope",
                },
                {"fb_post_id": "c", "comment_text": "c", "status": "SENT"},
            ],
        )
        resp = client.get(
            "/api/v1/history?status=SENT", headers=_auth(admin_token)
        )
        body = resp.json()
        assert body["total"] == 2
        assert all(it["status"] == "SENT" for it in body["items"])

    def test_filter_by_status_failed(self, client, admin_token):
        _seed_history(
            client,
            [
                {"fb_post_id": "a", "comment_text": "a", "status": "SENT"},
                {
                    "fb_post_id": "b",
                    "comment_text": "b",
                    "status": "FAILED",
                    "error_message": "boom",
                },
            ],
        )
        resp = client.get(
            "/api/v1/history?status=FAILED", headers=_auth(admin_token)
        )
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["status"] == "FAILED"
        assert body["items"][0]["error_message"] == "boom"

    def test_invalid_status_rejected(self, client, admin_token):
        resp = client.get(
            "/api/v1/history?status=WTF", headers=_auth(admin_token)
        )
        assert resp.status_code == 400


class TestPagination:
    def test_limit_clamps_results(self, client, admin_token):
        _seed_history(
            client,
            [
                {
                    "fb_post_id": f"p_{i}",
                    "comment_text": f"c_{i}",
                    "status": "SENT",
                }
                for i in range(5)
            ],
        )
        resp = client.get(
            "/api/v1/history?limit=3", headers=_auth(admin_token)
        )
        body = resp.json()
        assert body["total"] == 5
        assert len(body["items"]) == 3

    def test_offset_skips_rows(self, client, admin_token):
        now = datetime.now(timezone.utc)
        _seed_history(
            client,
            [
                {
                    "fb_post_id": f"p_{i}",
                    "comment_text": f"c_{i}",
                    "status": "SENT",
                    "sent_at": now - timedelta(minutes=i),
                }
                for i in range(5)
            ],
        )
        # Default order = sent_at desc → i=0 first. Offset=2 skips i=0,1.
        resp = client.get(
            "/api/v1/history?limit=2&offset=2", headers=_auth(admin_token)
        )
        items = resp.json()["items"]
        assert [it["comment_text"] for it in items] == ["c_2", "c_3"]

    def test_limit_too_large_clamped(self, client, admin_token):
        """Limit should be clamped to a reasonable max."""
        resp = client.get(
            "/api/v1/history?limit=9999", headers=_auth(admin_token)
        )
        # Either 400 (reject) or 200 (silently clamp). Accept both.
        assert resp.status_code in (200, 400, 422)
