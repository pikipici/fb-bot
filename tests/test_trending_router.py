"""Router tests for /api/v1/trending — list trending posts with filters."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server import auth as auth_module
from server import database as database_module
from server.database import Base, get_db
from server.models import Source, TrendingPost


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-please-change")
    monkeypatch.setenv(
        "CREDENTIALS_KEY", "WyzJqG3Vg9ZpUyFkq4bUxN9yxMG3xCyq4Rr8s3fL7dE="
    )
    monkeypatch.setenv("ENV", "development")
    auth_module._reset_jwt_secret_cache_for_tests()

    engine = create_engine(
        f"sqlite:///{tmp_path}/test_trending.db",
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


def _seed_posts(client: TestClient, posts_spec: list[dict]) -> dict[str, int]:
    """Insert sources + trending posts directly via the test session.

    ``posts_spec`` entries:
        source_type, source_label, fb_post_id, status, score,
        reactions_total, author, text, [post_timestamp]

    Returns a dict mapping ``(source_type, source_label)`` → source_id.
    """
    SessionLocal = client._session_factory  # type: ignore[attr-defined]
    source_ids: dict[tuple[str, str], int] = {}
    with SessionLocal() as db:
        for spec in posts_spec:
            key = (spec["source_type"], spec["source_label"])
            if key not in source_ids:
                src = Source(
                    type=spec["source_type"],
                    label=spec["source_label"],
                    fb_entity_id=spec.get("fb_entity_id"),
                    enabled=True,
                )
                db.add(src)
                db.flush()
                source_ids[key] = src.id
            post = TrendingPost(
                fb_post_id=spec["fb_post_id"],
                source_id=source_ids[key],
                author_name=spec.get("author", "someone"),
                text_snippet=spec.get("text", "hello world"),
                post_url=spec.get("post_url", "https://fb.com/p/1"),
                thumbnail_url=spec.get("thumbnail_url"),
                likes=spec.get("likes", 0),
                comments=spec.get("comments", 0),
                shares=spec.get("shares", 0),
                reactions_total=spec.get("reactions_total", 0),
                score=spec.get("score", 0.0),
                velocity=spec.get("velocity", 0.0),
                status=spec.get("status", "NEW"),
                post_timestamp=spec.get("post_timestamp"),
            )
            db.add(post)
        db.commit()
    return {f"{t}|{l}": v for (t, l), v in source_ids.items()}


# --- auth guards ---------------------------------------------------------


class TestAuthGuards:
    def test_unauthenticated_list_rejected(self, client):
        resp = client.get("/api/v1/trending")
        assert resp.status_code in (401, 403)

    def test_viewer_allowed_to_list(self, client, viewer_token):
        # Trending is a read-only feed; viewer role should be allowed.
        resp = client.get("/api/v1/trending", headers=_auth(viewer_token))
        assert resp.status_code == 200


# --- list + ordering -----------------------------------------------------


class TestListTrending:
    def test_empty_list(self, client, admin_token):
        resp = client.get("/api/v1/trending", headers=_auth(admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["posts"] == []
        assert body["total"] == 0

    def test_orders_by_score_desc_by_default(self, client, admin_token):
        _seed_posts(
            client,
            [
                {"source_type": "home_feed", "source_label": "Beranda",
                 "fb_post_id": "p_low", "score": 10.0, "reactions_total": 10},
                {"source_type": "home_feed", "source_label": "Beranda",
                 "fb_post_id": "p_high", "score": 9999.0, "reactions_total": 9999},
                {"source_type": "home_feed", "source_label": "Beranda",
                 "fb_post_id": "p_mid", "score": 500.0, "reactions_total": 500},
            ],
        )
        resp = client.get("/api/v1/trending", headers=_auth(admin_token))
        assert resp.status_code == 200
        posts = resp.json()["posts"]
        assert [p["fb_post_id"] for p in posts] == ["p_high", "p_mid", "p_low"]

    def test_embeds_source_label_and_type(self, client, admin_token):
        _seed_posts(
            client,
            [
                {"source_type": "group", "source_label": "Jual Beli JKT",
                 "fb_entity_id": "1234", "fb_post_id": "pg1",
                 "score": 100.0, "reactions_total": 100},
            ],
        )
        resp = client.get("/api/v1/trending", headers=_auth(admin_token))
        post = resp.json()["posts"][0]
        assert post["source"]["type"] == "group"
        assert post["source"]["label"] == "Jual Beli JKT"
        assert post["source"]["id"] is not None

    def test_exposes_engagement_fields(self, client, admin_token):
        _seed_posts(
            client,
            [
                {"source_type": "home_feed", "source_label": "Beranda",
                 "fb_post_id": "p1",
                 "likes": 120, "comments": 30, "shares": 5,
                 "reactions_total": 155, "score": 88.5, "velocity": 72.3,
                 "author": "Contoh User", "text": "halo dunia"},
            ],
        )
        resp = client.get("/api/v1/trending", headers=_auth(admin_token))
        post = resp.json()["posts"][0]
        assert post["likes"] == 120
        assert post["comments"] == 30
        assert post["shares"] == 5
        assert post["reactions_total"] == 155
        assert post["score"] == 88.5
        assert post["velocity"] == 72.3
        assert post["author_name"] == "Contoh User"
        assert post["text_snippet"] == "halo dunia"
        assert post["status"] == "NEW"

    def test_total_counts_all_rows_matching_filters(self, client, admin_token):
        _seed_posts(
            client,
            [
                {"source_type": "home_feed", "source_label": "Beranda",
                 "fb_post_id": f"p{i}", "score": float(i),
                 "reactions_total": i} for i in range(1, 8)
            ],
        )
        resp = client.get(
            "/api/v1/trending?limit=3", headers=_auth(admin_token)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["posts"]) == 3
        assert body["total"] == 7


# --- filters -------------------------------------------------------------


class TestFilters:
    def test_filter_by_status(self, client, admin_token):
        _seed_posts(
            client,
            [
                {"source_type": "home_feed", "source_label": "Beranda",
                 "fb_post_id": "pn", "status": "NEW", "score": 100,
                 "reactions_total": 100},
                {"source_type": "home_feed", "source_label": "Beranda",
                 "fb_post_id": "pd", "status": "DRAFTED", "score": 50,
                 "reactions_total": 50},
                {"source_type": "home_feed", "source_label": "Beranda",
                 "fb_post_id": "ps", "status": "SKIPPED", "score": 30,
                 "reactions_total": 30},
            ],
        )
        resp = client.get(
            "/api/v1/trending?status=NEW", headers=_auth(admin_token)
        )
        assert [p["fb_post_id"] for p in resp.json()["posts"]] == ["pn"]

    def test_filter_by_source_id(self, client, admin_token):
        ids = _seed_posts(
            client,
            [
                {"source_type": "home_feed", "source_label": "Beranda",
                 "fb_post_id": "h1", "score": 100, "reactions_total": 100},
                {"source_type": "group", "source_label": "GrupA",
                 "fb_entity_id": "1", "fb_post_id": "g1", "score": 200,
                 "reactions_total": 200},
                {"source_type": "group", "source_label": "GrupB",
                 "fb_entity_id": "2", "fb_post_id": "g2", "score": 300,
                 "reactions_total": 300},
            ],
        )
        grup_a_id = ids["group|GrupA"]
        resp = client.get(
            f"/api/v1/trending?source_id={grup_a_id}",
            headers=_auth(admin_token),
        )
        assert [p["fb_post_id"] for p in resp.json()["posts"]] == ["g1"]

    def test_sort_by_velocity(self, client, admin_token):
        _seed_posts(
            client,
            [
                {"source_type": "home_feed", "source_label": "Beranda",
                 "fb_post_id": "slow", "score": 9999, "velocity": 1,
                 "reactions_total": 9999},
                {"source_type": "home_feed", "source_label": "Beranda",
                 "fb_post_id": "fast", "score": 1, "velocity": 999,
                 "reactions_total": 1},
            ],
        )
        resp = client.get(
            "/api/v1/trending?sort=velocity", headers=_auth(admin_token)
        )
        assert [p["fb_post_id"] for p in resp.json()["posts"]] == ["fast", "slow"]

    def test_sort_by_recent_uses_collected_at(self, client, admin_token):
        # Seed then mutate collected_at to guarantee ordering.
        _seed_posts(
            client,
            [
                {"source_type": "home_feed", "source_label": "Beranda",
                 "fb_post_id": "old", "score": 100, "reactions_total": 100},
                {"source_type": "home_feed", "source_label": "Beranda",
                 "fb_post_id": "new", "score": 10, "reactions_total": 10},
            ],
        )
        SessionLocal = client._session_factory  # type: ignore[attr-defined]
        now = datetime.now(timezone.utc)
        with SessionLocal() as db:
            for row in db.query(TrendingPost).all():
                row.collected_at = (
                    now - timedelta(hours=2)
                    if row.fb_post_id == "old"
                    else now
                )
            db.commit()
        resp = client.get(
            "/api/v1/trending?sort=recent", headers=_auth(admin_token)
        )
        assert [p["fb_post_id"] for p in resp.json()["posts"]] == ["new", "old"]

    def test_invalid_sort_rejected(self, client, admin_token):
        resp = client.get(
            "/api/v1/trending?sort=bogus", headers=_auth(admin_token)
        )
        assert resp.status_code == 400

    def test_invalid_status_rejected(self, client, admin_token):
        resp = client.get(
            "/api/v1/trending?status=INVALID", headers=_auth(admin_token)
        )
        assert resp.status_code == 400

    def test_limit_clamped_to_sane_bounds(self, client, admin_token):
        # Seed 3 rows, request limit=9999 — should still return 3 rows OK.
        _seed_posts(
            client,
            [
                {"source_type": "home_feed", "source_label": "Beranda",
                 "fb_post_id": f"p{i}", "score": float(i),
                 "reactions_total": i} for i in range(3)
            ],
        )
        resp = client.get(
            "/api/v1/trending?limit=9999", headers=_auth(admin_token)
        )
        assert resp.status_code == 200
        assert len(resp.json()["posts"]) == 3


class TestGenerateDraft:
    def test_generates_draft_from_active_template(self, client, admin_token):
        _seed_posts(
            client,
            [
                {"source_type": "home_feed", "source_label": "Beranda",
                 "fb_post_id": "p1", "score": 100, "reactions_total": 100,
                 "author": "Budi", "text": "jual laptop gaming"},
            ],
        )
        # Set active template
        client.put(
            "/api/v1/template",
            json={"template_text": "Halo {author_name}, tertarik {text_snippet}?"},
            headers=_auth(admin_token),
        )
        # Resolve post id from DB
        SessionLocal = client._session_factory  # type: ignore[attr-defined]
        with SessionLocal() as db:
            post = db.query(TrendingPost).filter_by(fb_post_id="p1").first()
            pid = post.id

        resp = client.post(
            f"/api/v1/trending/{pid}/draft", headers=_auth(admin_token)
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["draft_text"] == "Halo Budi, tertarik jual laptop gaming?"
        assert body["post"]["status"] == "DRAFTED"

    def test_persists_status_transition(self, client, admin_token):
        _seed_posts(
            client,
            [
                {"source_type": "home_feed", "source_label": "Beranda",
                 "fb_post_id": "p2", "score": 50, "reactions_total": 50},
            ],
        )
        client.put(
            "/api/v1/template",
            json={"template_text": "hi"},
            headers=_auth(admin_token),
        )
        SessionLocal = client._session_factory  # type: ignore[attr-defined]
        with SessionLocal() as db:
            pid = db.query(TrendingPost).filter_by(fb_post_id="p2").first().id
        client.post(
            f"/api/v1/trending/{pid}/draft", headers=_auth(admin_token)
        )
        # Re-list should now show DRAFTED
        resp = client.get(
            "/api/v1/trending?status=DRAFTED", headers=_auth(admin_token)
        )
        assert [p["fb_post_id"] for p in resp.json()["posts"]] == ["p2"]

    def test_missing_template_returns_400(self, client, admin_token):
        _seed_posts(
            client,
            [
                {"source_type": "home_feed", "source_label": "Beranda",
                 "fb_post_id": "p3", "score": 50, "reactions_total": 50},
            ],
        )
        SessionLocal = client._session_factory  # type: ignore[attr-defined]
        with SessionLocal() as db:
            pid = db.query(TrendingPost).filter_by(fb_post_id="p3").first().id
        resp = client.post(
            f"/api/v1/trending/{pid}/draft", headers=_auth(admin_token)
        )
        assert resp.status_code == 400

    def test_unknown_post_returns_404(self, client, admin_token):
        client.put(
            "/api/v1/template",
            json={"template_text": "hi"},
            headers=_auth(admin_token),
        )
        resp = client.post(
            "/api/v1/trending/99999/draft", headers=_auth(admin_token)
        )
        assert resp.status_code == 404

    def test_already_commented_returns_409(self, client, admin_token):
        _seed_posts(
            client,
            [
                {"source_type": "home_feed", "source_label": "Beranda",
                 "fb_post_id": "p4", "score": 50, "reactions_total": 50,
                 "status": "COMMENTED"},
            ],
        )
        client.put(
            "/api/v1/template",
            json={"template_text": "hi"},
            headers=_auth(admin_token),
        )
        SessionLocal = client._session_factory  # type: ignore[attr-defined]
        with SessionLocal() as db:
            pid = db.query(TrendingPost).filter_by(fb_post_id="p4").first().id
        resp = client.post(
            f"/api/v1/trending/{pid}/draft", headers=_auth(admin_token)
        )
        assert resp.status_code == 409

    def test_skipped_can_be_redrafted(self, client, admin_token):
        # User skipped by mistake; drafting again is allowed.
        _seed_posts(
            client,
            [
                {"source_type": "home_feed", "source_label": "Beranda",
                 "fb_post_id": "p5", "score": 50, "reactions_total": 50,
                 "status": "SKIPPED"},
            ],
        )
        client.put(
            "/api/v1/template",
            json={"template_text": "hi bro"},
            headers=_auth(admin_token),
        )
        SessionLocal = client._session_factory  # type: ignore[attr-defined]
        with SessionLocal() as db:
            pid = db.query(TrendingPost).filter_by(fb_post_id="p5").first().id
        resp = client.post(
            f"/api/v1/trending/{pid}/draft", headers=_auth(admin_token)
        )
        assert resp.status_code == 200
        assert resp.json()["post"]["status"] == "DRAFTED"

    def test_drafted_post_redraft_regenerates_text(self, client, admin_token):
        # If user already drafted and template changed, they can regen.
        _seed_posts(
            client,
            [
                {"source_type": "home_feed", "source_label": "Beranda",
                 "fb_post_id": "p6", "score": 50, "reactions_total": 50,
                 "status": "DRAFTED"},
            ],
        )
        client.put(
            "/api/v1/template",
            json={"template_text": "template baru versi 2"},
            headers=_auth(admin_token),
        )
        SessionLocal = client._session_factory  # type: ignore[attr-defined]
        with SessionLocal() as db:
            pid = db.query(TrendingPost).filter_by(fb_post_id="p6").first().id
        resp = client.post(
            f"/api/v1/trending/{pid}/draft", headers=_auth(admin_token)
        )
        assert resp.status_code == 200
        assert resp.json()["draft_text"] == "template baru versi 2"

    def test_viewer_cannot_generate_draft(self, client, viewer_token, admin_token):
        _seed_posts(
            client,
            [
                {"source_type": "home_feed", "source_label": "Beranda",
                 "fb_post_id": "p7", "score": 50, "reactions_total": 50},
            ],
        )
        client.put(
            "/api/v1/template",
            json={"template_text": "hi"},
            headers=_auth(admin_token),
        )
        SessionLocal = client._session_factory  # type: ignore[attr-defined]
        with SessionLocal() as db:
            pid = db.query(TrendingPost).filter_by(fb_post_id="p7").first().id
        resp = client.post(
            f"/api/v1/trending/{pid}/draft", headers=_auth(viewer_token)
        )
        assert resp.status_code == 403


class TestSkipPost:
    def test_skip_transitions_to_skipped(self, client, admin_token):
        _seed_posts(
            client,
            [
                {"source_type": "home_feed", "source_label": "Beranda",
                 "fb_post_id": "s1", "score": 50, "reactions_total": 50},
            ],
        )
        SessionLocal = client._session_factory  # type: ignore[attr-defined]
        with SessionLocal() as db:
            pid = db.query(TrendingPost).filter_by(fb_post_id="s1").first().id
        resp = client.post(
            f"/api/v1/trending/{pid}/skip", headers=_auth(admin_token)
        )
        assert resp.status_code == 200
        assert resp.json()["post"]["status"] == "SKIPPED"

    def test_skip_commented_returns_409(self, client, admin_token):
        _seed_posts(
            client,
            [
                {"source_type": "home_feed", "source_label": "Beranda",
                 "fb_post_id": "s2", "score": 50, "reactions_total": 50,
                 "status": "COMMENTED"},
            ],
        )
        SessionLocal = client._session_factory  # type: ignore[attr-defined]
        with SessionLocal() as db:
            pid = db.query(TrendingPost).filter_by(fb_post_id="s2").first().id
        resp = client.post(
            f"/api/v1/trending/{pid}/skip", headers=_auth(admin_token)
        )
        assert resp.status_code == 409

    def test_skip_unknown_post_returns_404(self, client, admin_token):
        resp = client.post(
            "/api/v1/trending/99999/skip", headers=_auth(admin_token)
        )
        assert resp.status_code == 404


# --- Send Comment (F5) ----------------------------------------------------


def _seed_active_fb_account(client: TestClient, *, fb_name: str = "Digi Markt"):
    """Seed a minimal active FBAccount row with encrypted cookies."""
    from server.crypto import encrypt_cookies
    from server.models import FBAccount

    SessionLocal = client._session_factory  # type: ignore[attr-defined]
    with SessionLocal() as db:
        acc = FBAccount(
            label="test acc",
            status="ACTIVE",
            cookies_encrypted=encrypt_cookies({"c_user": "1", "xs": "y"}),
            fb_name=fb_name,
        )
        db.add(acc)
        db.commit()
        return acc.id


class TestSendComment:
    def _seed_drafted(self, client):
        _seed_posts(
            client,
            [
                {
                    "source_type": "home_feed",
                    "source_label": "Beranda",
                    "fb_post_id": "send1",
                    "score": 50,
                    "reactions_total": 50,
                    "status": "DRAFTED",
                    "post_url": "https://www.facebook.com/p/1",
                    "author": "Someone",
                }
            ],
        )
        SessionLocal = client._session_factory  # type: ignore[attr-defined]
        with SessionLocal() as db:
            from server.models import TrendingPost as TP

            return db.query(TP).filter_by(fb_post_id="send1").first().id

    def test_unauth_rejected(self, client):
        resp = client.post(
            "/api/v1/trending/1/comment", json={"comment_text": "hi"}
        )
        assert resp.status_code in (401, 403)

    def test_viewer_rejected(self, client, viewer_token):
        resp = client.post(
            "/api/v1/trending/1/comment",
            json={"comment_text": "hi"},
            headers=_auth(viewer_token),
        )
        assert resp.status_code == 403

    def test_unknown_post_returns_404(
        self, client, admin_token, monkeypatch
    ):
        _seed_active_fb_account(client)
        resp = client.post(
            "/api/v1/trending/99999/comment",
            json={"comment_text": "hi"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 404

    def test_empty_comment_rejected(self, client, admin_token):
        _seed_active_fb_account(client)
        pid = self._seed_drafted(client)
        resp = client.post(
            f"/api/v1/trending/{pid}/comment",
            json={"comment_text": "   "},
            headers=_auth(admin_token),
        )
        # 400 from our validator, or 422 from pydantic min_length
        assert resp.status_code in (400, 422)

    @pytest.mark.parametrize(
        "bad_url,kind",
        [
            (
                "https://www.facebook.com/stories/122112357512213503/UzpfSVNDOjE=",
                "stories",
            ),
            (
                "https://www.facebook.com/reel/1234567890",
                "reel",
            ),
            (
                "https://www.facebook.com/watch/?v=1234567890",
                "watch",
            ),
            (
                "https://www.facebook.com/share/r/abcDEF123/",
                "share_reel",
            ),
        ],
    )
    def test_unsupported_post_url_rejected(
        self, client, admin_token, bad_url, kind
    ):
        """Stories / reels / watch URLs should 415 before Playwright runs."""
        _seed_active_fb_account(client)
        _seed_posts(
            client,
            [
                {
                    "source_type": "home_feed",
                    "source_label": "Beranda",
                    "fb_post_id": f"bad_{kind}",
                    "score": 50,
                    "reactions_total": 50,
                    "status": "DRAFTED",
                    "post_url": bad_url,
                    "author": "Stranger",
                }
            ],
        )
        SessionLocal = client._session_factory  # type: ignore[attr-defined]
        with SessionLocal() as db:
            from server.models import TrendingPost as TP

            pid = (
                db.query(TP).filter_by(fb_post_id=f"bad_{kind}").first().id
            )

        # Spy on send_comment — it must NOT be invoked for unsupported URLs.
        called = {"hit": False}

        async def _fake_send(**_kwargs):
            called["hit"] = True
            raise AssertionError(
                "send_comment should not run for unsupported URL"
            )

        import server.routers.trending as trending_mod

        monkeypatch_target = getattr(trending_mod, "send_comment", None)
        trending_mod.send_comment = _fake_send  # type: ignore[assignment]
        try:
            resp = client.post(
                f"/api/v1/trending/{pid}/comment",
                json={"comment_text": "halo"},
                headers=_auth(admin_token),
            )
        finally:
            trending_mod.send_comment = monkeypatch_target  # type: ignore[assignment]

        assert resp.status_code == 415, resp.text
        body = resp.json()
        # Error body should mention the unsupported type so UI can surface it.
        detail = (body.get("detail") or "").lower()
        assert (
            "stories" in detail
            or "reel" in detail
            or "watch" in detail
            or "tidak didukung" in detail
            or "not supported" in detail
        )
        assert called["hit"] is False

    def test_already_commented_returns_409(self, client, admin_token):
        _seed_active_fb_account(client)
        _seed_posts(
            client,
            [
                {
                    "source_type": "home_feed",
                    "source_label": "Beranda",
                    "fb_post_id": "send_done",
                    "score": 50,
                    "reactions_total": 50,
                    "status": "COMMENTED",
                }
            ],
        )
        SessionLocal = client._session_factory  # type: ignore[attr-defined]
        with SessionLocal() as db:
            from server.models import TrendingPost as TP

            pid = db.query(TP).filter_by(fb_post_id="send_done").first().id
        resp = client.post(
            f"/api/v1/trending/{pid}/comment",
            json={"comment_text": "hi"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 409

    def test_no_active_fb_account_returns_503(self, client, admin_token):
        pid = self._seed_drafted(client)
        # NOTE: no FBAccount seeded
        resp = client.post(
            f"/api/v1/trending/{pid}/comment",
            json={"comment_text": "halo"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 503

    def test_happy_path_success(self, client, admin_token, monkeypatch):
        from bot.modules.comment_sender import SendResult

        async def _fake_send(**kwargs):
            return SendResult(
                success=True,
                comment_text=kwargs["comment_text"],
                post_url=kwargs["post_url"],
                fb_comment_id="cmt_happy",
            )

        monkeypatch.setattr(
            "server.routers.trending.send_comment", _fake_send
        )

        _seed_active_fb_account(client)
        pid = self._seed_drafted(client)

        resp = client.post(
            f"/api/v1/trending/{pid}/comment",
            json={"comment_text": "halo bro"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["result"]["success"] is True
        assert body["result"]["fb_comment_id"] == "cmt_happy"
        assert body["post"]["status"] == "COMMENTED"
        assert body["quota"]["used"] == 1
        assert body["quota"]["remaining"] == 4

        # Verify CommentHistory row inserted with SENT
        SessionLocal = client._session_factory  # type: ignore[attr-defined]
        with SessionLocal() as db:
            from server.models import CommentHistory

            rows = db.query(CommentHistory).all()
            assert len(rows) == 1
            assert rows[0].status == "SENT"
            assert rows[0].comment_text == "halo bro"
            assert rows[0].fb_comment_id == "cmt_happy"

    def test_rate_limit_blocks_before_send(
        self, client, admin_token, monkeypatch
    ):
        sent_flag = {"called": False}

        async def _fake_send(**kwargs):
            sent_flag["called"] = True
            return None  # would crash if called

        monkeypatch.setattr(
            "server.routers.trending.send_comment", _fake_send
        )

        _seed_active_fb_account(client)
        pid = self._seed_drafted(client)

        # Pre-seed 5 SENT rows to exhaust quota
        from datetime import datetime, timedelta, timezone

        from server.models import CommentHistory

        SessionLocal = client._session_factory  # type: ignore[attr-defined]
        with SessionLocal() as db:
            for i in range(5):
                db.add(
                    CommentHistory(
                        trending_post_id=pid,
                        comment_text="x",
                        status="SENT",
                        sent_at=datetime.now(timezone.utc)
                        - timedelta(minutes=10 + i),
                    )
                )
            db.commit()

        resp = client.post(
            f"/api/v1/trending/{pid}/comment",
            json={"comment_text": "ke-6 ya"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 429
        assert sent_flag["called"] is False

    def test_sender_failure_logs_failed_no_status_flip(
        self, client, admin_token, monkeypatch
    ):
        from bot.modules.comment_sender import SendResult

        async def _fake_send(**kwargs):
            return SendResult(
                success=False,
                comment_text=kwargs["comment_text"],
                post_url=kwargs["post_url"],
                fb_comment_id=None,
                error="composer ga ketemu",
            )

        monkeypatch.setattr(
            "server.routers.trending.send_comment", _fake_send
        )

        _seed_active_fb_account(client)
        pid = self._seed_drafted(client)

        resp = client.post(
            f"/api/v1/trending/{pid}/comment",
            json={"comment_text": "halo"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 502
        body = resp.json()
        assert "composer" in (body.get("detail") or "").lower()

        SessionLocal = client._session_factory  # type: ignore[attr-defined]
        with SessionLocal() as db:
            from server.models import CommentHistory, TrendingPost as TP

            rows = db.query(CommentHistory).all()
            assert len(rows) == 1
            assert rows[0].status == "FAILED"
            # Post status should NOT flip
            post = db.query(TP).filter_by(id=pid).first()
            assert post.status == "DRAFTED"

    def test_cookie_expired_marks_account_and_returns_503(
        self, client, admin_token, monkeypatch
    ):
        from bot.modules.comment_sender import CookieExpiredError

        async def _fake_send(**kwargs):
            raise CookieExpiredError("redirect ke login")

        monkeypatch.setattr(
            "server.routers.trending.send_comment", _fake_send
        )

        acc_id = _seed_active_fb_account(client)
        pid = self._seed_drafted(client)

        resp = client.post(
            f"/api/v1/trending/{pid}/comment",
            json={"comment_text": "halo"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 503

        SessionLocal = client._session_factory  # type: ignore[attr-defined]
        with SessionLocal() as db:
            from server.models import FBAccount

            acc = db.query(FBAccount).filter_by(id=acc_id).first()
            assert acc.status == "EXPIRED"

    def test_checkpoint_marks_account_and_returns_503(
        self, client, admin_token, monkeypatch
    ):
        from bot.modules.comment_sender import CheckpointRequiredError

        async def _fake_send(**kwargs):
            raise CheckpointRequiredError("checkpoint required")

        monkeypatch.setattr(
            "server.routers.trending.send_comment", _fake_send
        )

        acc_id = _seed_active_fb_account(client)
        pid = self._seed_drafted(client)

        resp = client.post(
            f"/api/v1/trending/{pid}/comment",
            json={"comment_text": "halo"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 503

        SessionLocal = client._session_factory  # type: ignore[attr-defined]
        with SessionLocal() as db:
            from server.models import FBAccount

            acc = db.query(FBAccount).filter_by(id=acc_id).first()
            assert acc.status == "CHECKPOINT"
