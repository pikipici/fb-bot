"""Router tests for /api/v1/scanner — status + manual run-now."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server import auth as auth_module
from server import database as database_module
from server.database import Base, get_db
from server.models import ScannerRun


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-please-change")
    monkeypatch.setenv(
        "CREDENTIALS_KEY", "WyzJqG3Vg9ZpUyFkq4bUxN9yxMG3xCyq4Rr8s3fL7dE="
    )
    monkeypatch.setenv("ENV", "development")
    auth_module._reset_jwt_secret_cache_for_tests()

    engine = create_engine(
        f"sqlite:///{tmp_path}/test_scanner.db",
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


def _seed_run(client, **kwargs):
    session = client._session_factory()  # type: ignore[attr-defined]
    try:
        defaults = {
            "trigger": "beat",
            "status": "success",
            "started_at": datetime.now(timezone.utc) - timedelta(minutes=5),
            "finished_at": datetime.now(timezone.utc) - timedelta(minutes=4),
            "enabled_sources": 2,
            "successful_scans": 2,
            "scan_errors": 0,
            "inserted": 4,
            "updated": 0,
            "skipped": 9,
        }
        defaults.update(kwargs)
        run = ScannerRun(**defaults)
        session.add(run)
        session.commit()
        session.refresh(run)
        return run.id
    finally:
        session.close()


class TestScannerStatus:
    def test_requires_auth(self, client):
        resp = client.get("/api/v1/scanner/status")
        # HTTPBearer returns 403 when no Authorization header present;
        # 401 comes from downstream token verification (bad/expired).
        assert resp.status_code in (401, 403)

    def test_empty_history_returns_null_runs(self, client):
        token = _register_and_login(client, "u", "Abcdef123!")
        resp = client.get(
            "/api/v1/scanner/status",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_running"] is False
        assert body["last_run"] is None
        assert body["last_success"] is None

    def test_returns_latest_run_and_last_success(self, client):
        token = _register_and_login(client, "u", "Abcdef123!")
        _seed_run(client, status="success", inserted=4)
        running_id = _seed_run(
            client,
            status="running",
            finished_at=None,
            inserted=0,
        )

        resp = client.get(
            "/api/v1/scanner/status",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_running"] is True
        assert body["last_run"]["id"] == running_id
        assert body["last_run"]["status"] == "running"
        # last_success should still reference the previous success row
        assert body["last_success"] is not None
        assert body["last_success"]["status"] == "success"
        assert body["last_success"]["inserted"] == 4

    def test_viewer_role_can_read(self, client):
        # First user becomes admin; second registers as viewer by default.
        admin_token = _register_and_login(client, "admin", "Abcdef123!")
        viewer_token = _register_and_login(
            client, "viewer", "Abcdef123!", role="viewer", admin_token=admin_token
        )
        _seed_run(client, inserted=7)

        resp = client.get(
            "/api/v1/scanner/status",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["last_run"]["inserted"] == 7


class TestScannerRunNow:
    def test_requires_auth(self, client):
        resp = client.post("/api/v1/scanner/run-now")
        assert resp.status_code in (401, 403)

    def test_non_admin_rejected(self, client):
        admin_token = _register_and_login(client, "admin", "Abcdef123!")
        viewer_token = _register_and_login(
            client, "viewer", "Abcdef123!", role="viewer", admin_token=admin_token
        )
        resp = client.post(
            "/api/v1/scanner/run-now",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert resp.status_code == 403

    def test_admin_enqueues_task(self, client):
        admin_token = _register_and_login(client, "admin", "Abcdef123!")

        fake_task = MagicMock()
        fake_result = MagicMock()
        fake_result.id = "fake-task-id"
        fake_task.delay.return_value = fake_result

        with patch("bot.tasks.scan_all_sources", fake_task):
            resp = client.post(
                "/api/v1/scanner/run-now",
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["task_id"] == "fake-task-id"
        assert body["trigger"] == "manual"
        fake_task.delay.assert_called_once_with(trigger="manual")

    def test_conflict_when_recent_run_in_progress(self, client):
        admin_token = _register_and_login(client, "admin", "Abcdef123!")
        # Seed a running row started 30s ago — should 409
        _seed_run(
            client,
            status="running",
            finished_at=None,
            started_at=datetime.now(timezone.utc) - timedelta(seconds=30),
        )

        resp = client.post(
            "/api/v1/scanner/run-now",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 409
        assert "jalan" in resp.json()["detail"].lower()

    def test_stale_running_row_is_ignored(self, client):
        """Running row older than 15 min = stuck worker, allow new run."""
        admin_token = _register_and_login(client, "admin", "Abcdef123!")
        _seed_run(
            client,
            status="running",
            finished_at=None,
            started_at=datetime.now(timezone.utc) - timedelta(minutes=20),
        )

        fake_task = MagicMock()
        fake_result = MagicMock()
        fake_result.id = "new-task"
        fake_task.delay.return_value = fake_result

        with patch("bot.tasks.scan_all_sources", fake_task):
            resp = client.post(
                "/api/v1/scanner/run-now",
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert resp.status_code == 202
        fake_task.delay.assert_called_once()

    def test_celery_enqueue_failure_returns_503(self, client):
        admin_token = _register_and_login(client, "admin", "Abcdef123!")

        fake_task = MagicMock()
        fake_task.delay.side_effect = RuntimeError("broker down")

        with patch("bot.tasks.scan_all_sources", fake_task):
            resp = client.post(
                "/api/v1/scanner/run-now",
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert resp.status_code == 503
        assert "Celery" in resp.json()["detail"]
