"""Phase K-5 — auto_comment_next dry-run mode tests.

Dry-run: AI draft tetap generate (validasi quality), tapi:
  - send_comment() SKIP total (zero FB API call)
  - CommentHistory row inserted dengan status='DRAFT' (audit trail)
  - RateLimitService.record_send TIDAK dipanggil (quota tidak terbakar)
  - Self-reschedule TETAP fire di finally-block
  - Result {action: 'draft', reason: 'dry_run', post_id, draft}

Switch via env ``AUTO_COMMENT_DRY_RUN=1``. Default code = ``False`` (live mode);
deploy explicit set env biar production yang explicit, bukan code yang opaque.

Natural dedup: CommentHistory row exists → next tick eligibility query skip
post tsb. Saat flip ke live, post yg pernah DRAFT'd ga akan retroaktif di-send
(post udah stale by then).
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.database import Base
from server.models import (
    CommentHistory,
    FBAccount,
    Source,
    TrendingPost,
)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "CREDENTIALS_KEY", "WyzJqG3Vg9ZpUyFkq4bUxN9yxMG3xCyq4Rr8s3fL7dE="
    )
    monkeypatch.setenv("SUMOPOD_API_KEY", "test-key")
    monkeypatch.delenv("AUTO_COMMENT_DISABLED", raising=False)
    monkeypatch.delenv("AUTO_COMMENT_DRY_RUN", raising=False)
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _ctx_session(session):
    from contextlib import contextmanager

    @contextmanager
    def _cm():
        yield session

    return _cm()


def _seed_account(db, *, status: str = "ACTIVE") -> FBAccount:
    acc = FBAccount(
        label="khorur",
        status=status,
        cookies_encrypted="dummy_encrypted_blob",
        fb_user_id="61577777450562",
        fb_name="Digi Markt",
    )
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return acc


def _seed_post(
    db, *, fb_post_id: str = "p1", status: str = "NEW"
) -> TrendingPost:
    src = db.query(Source).first()
    if src is None:
        src = Source(type="home_feed", label="beranda", enabled=True)
        db.add(src)
        db.commit()
        db.refresh(src)
    post = TrendingPost(
        fb_post_id=fb_post_id,
        source_id=src.id,
        author_name="X",
        text_snippet="hello",
        post_url=f"https://www.facebook.com/test/{fb_post_id}",
        status=status,
        collected_at=datetime.now(timezone.utc),
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    return post


def _patch_common(monkeypatch, db):
    """Wire _db_session + capture apply_async without firing celery."""
    monkeypatch.setattr("bot.tasks._db_session", lambda: _ctx_session(db))
    apply_async = MagicMock()
    monkeypatch.setattr(
        "bot.tasks.auto_comment_next.apply_async", apply_async
    )
    return apply_async


def _stub_ai_draft(monkeypatch, draft_text: str = "halo bro mantap banget"):
    """Stub AIDraftService.generate to return fixed text."""
    generate_mock = MagicMock(return_value=draft_text)

    class _StubAI:
        def __init__(self, _db):
            pass

        def generate(self, **kwargs):
            return generate_mock(**kwargs)

    monkeypatch.setattr(
        "server.services.ai_draft_service.AIDraftService", _StubAI
    )
    monkeypatch.setattr(
        "bot.tasks.decrypt_cookies",
        lambda blob: {"c_user": "1", "xs": "x"},
    )
    return generate_mock


# ---------------------------------------------------------------------------
# _auto_comment_dry_run() helper in celery_app
# ---------------------------------------------------------------------------


class TestAutoCommentDryRunHelper:
    def test_default_false(self, monkeypatch):
        from bot import celery_app

        monkeypatch.delenv("AUTO_COMMENT_DRY_RUN", raising=False)
        assert celery_app._auto_comment_dry_run() is False

    def test_env_1_returns_true(self, monkeypatch):
        from bot import celery_app

        monkeypatch.setenv("AUTO_COMMENT_DRY_RUN", "1")
        assert celery_app._auto_comment_dry_run() is True

    def test_env_0_returns_false(self, monkeypatch):
        from bot import celery_app

        monkeypatch.setenv("AUTO_COMMENT_DRY_RUN", "0")
        assert celery_app._auto_comment_dry_run() is False

    def test_env_other_value_returns_false(self, monkeypatch):
        from bot import celery_app

        # Defensive: anything not exactly "1" stays false (safer for live).
        monkeypatch.setenv("AUTO_COMMENT_DRY_RUN", "true")
        assert celery_app._auto_comment_dry_run() is False


# ---------------------------------------------------------------------------
# auto_comment_next dry-run branch
# ---------------------------------------------------------------------------


class TestAutoCommentNextDryRun:
    """All tests assume AUTO_COMMENT_DRY_RUN=1 unless noted."""

    def test_dry_run_skips_send_comment(self, monkeypatch, db):
        """send_comment() never called when dry-run on."""
        from bot import tasks

        monkeypatch.setenv("AUTO_COMMENT_DRY_RUN", "1")
        _seed_account(db)
        _seed_post(db)
        apply_async = _patch_common(monkeypatch, db)
        _stub_ai_draft(monkeypatch)

        async def _must_not_call(**_kwargs):
            raise AssertionError(
                "send_comment must NOT be called in dry-run mode"
            )

        monkeypatch.setattr("bot.tasks.send_comment", _must_not_call)

        # Should not raise — branch fires before send_comment.
        result = tasks.auto_comment_next()

        assert result["action"] == "draft"
        apply_async.assert_called_once()

    def test_dry_run_logs_draft_status(self, monkeypatch, db):
        """CommentHistory row inserted with status='DRAFT' + draft text."""
        from bot import tasks

        monkeypatch.setenv("AUTO_COMMENT_DRY_RUN", "1")
        _seed_account(db)
        _seed_post(db)
        _patch_common(monkeypatch, db)
        _stub_ai_draft(monkeypatch, draft_text="komen test dari AI")

        async def _must_not_call(**_kwargs):
            raise AssertionError("send_comment must NOT run")

        monkeypatch.setattr("bot.tasks.send_comment", _must_not_call)

        tasks.auto_comment_next()

        rows = db.query(CommentHistory).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.status == "DRAFT"
        assert row.comment_text == "komen test dari AI"
        assert row.fb_comment_id is None
        assert row.error_message is None

    def test_dry_run_still_generates_ai_draft(self, monkeypatch, db):
        """AI draft generation tetap dipanggil (the whole point)."""
        from bot import tasks

        monkeypatch.setenv("AUTO_COMMENT_DRY_RUN", "1")
        _seed_account(db)
        _seed_post(db)
        _patch_common(monkeypatch, db)
        ai_mock = _stub_ai_draft(monkeypatch)

        async def _must_not_call(**_kwargs):
            raise AssertionError("send_comment must NOT run")

        monkeypatch.setattr("bot.tasks.send_comment", _must_not_call)

        tasks.auto_comment_next()

        ai_mock.assert_called_once()

    def test_dry_run_still_self_reschedules(self, monkeypatch, db):
        """Finally-block tetap fire countdown buat tick berikutnya."""
        from bot import tasks

        monkeypatch.setenv("AUTO_COMMENT_DRY_RUN", "1")
        _seed_account(db)
        _seed_post(db)
        apply_async = _patch_common(monkeypatch, db)
        _stub_ai_draft(monkeypatch)

        async def _must_not_call(**_kwargs):
            raise AssertionError("send_comment must NOT run")

        monkeypatch.setattr("bot.tasks.send_comment", _must_not_call)

        tasks.auto_comment_next()

        apply_async.assert_called_once()
        kwargs = apply_async.call_args.kwargs
        assert kwargs["kwargs"] == {"trigger": "selfsched"}
        assert "countdown" in kwargs

    def test_dry_run_returns_draft_action(self, monkeypatch, db):
        """Result dict identifiable buat observability."""
        from bot import tasks

        monkeypatch.setenv("AUTO_COMMENT_DRY_RUN", "1")
        _seed_account(db)
        post = _seed_post(db)
        _patch_common(monkeypatch, db)
        _stub_ai_draft(monkeypatch, draft_text="apa kabar bro")

        async def _must_not_call(**_kwargs):
            raise AssertionError("send_comment must NOT run")

        monkeypatch.setattr("bot.tasks.send_comment", _must_not_call)

        result = tasks.auto_comment_next()

        assert result["action"] == "draft"
        assert result["reason"] == "dry_run"
        assert result["post_id"] == post.id
        assert result["draft"] == "apa kabar bro"

    def test_dry_run_does_not_burn_quota(self, monkeypatch, db):
        """RateLimitService.record_send NOT called → quota window unchanged."""
        from bot import tasks

        monkeypatch.setenv("AUTO_COMMENT_DRY_RUN", "1")
        _seed_account(db)
        _seed_post(db)
        _patch_common(monkeypatch, db)
        _stub_ai_draft(monkeypatch)

        async def _must_not_call(**_kwargs):
            raise AssertionError("send_comment must NOT run")

        monkeypatch.setattr("bot.tasks.send_comment", _must_not_call)

        tasks.auto_comment_next()

        # Zero SENT rows means RateLimitService quota query returns used=0.
        sent_rows = (
            db.query(CommentHistory)
            .filter(CommentHistory.status == "SENT")
            .all()
        )
        assert len(sent_rows) == 0

    def test_dry_run_post_status_remains_new(self, monkeypatch, db):
        """Post.status TIDAK flip ke COMMENTED (cuma natural dedup via CH row)."""
        from bot import tasks

        monkeypatch.setenv("AUTO_COMMENT_DRY_RUN", "1")
        _seed_account(db)
        post = _seed_post(db)
        _patch_common(monkeypatch, db)
        _stub_ai_draft(monkeypatch)

        async def _must_not_call(**_kwargs):
            raise AssertionError("send_comment must NOT run")

        monkeypatch.setattr("bot.tasks.send_comment", _must_not_call)

        tasks.auto_comment_next()

        db.refresh(post)
        # Post tetap NEW. Eligibility query exclude via CommentHistory join.
        assert post.status == "NEW"

    def test_dry_run_dedup_on_next_tick(self, monkeypatch, db):
        """Tick kedua dengan post yang sama → no_eligible (CH row exists)."""
        from bot import tasks

        monkeypatch.setenv("AUTO_COMMENT_DRY_RUN", "1")
        _seed_account(db)
        _seed_post(db)  # only 1 post
        _patch_common(monkeypatch, db)
        _stub_ai_draft(monkeypatch)

        async def _must_not_call(**_kwargs):
            raise AssertionError("send_comment must NOT run")

        monkeypatch.setattr("bot.tasks.send_comment", _must_not_call)

        # Tick 1: drafts post p1.
        result1 = tasks.auto_comment_next()
        assert result1["action"] == "draft"

        # Tick 2: should find no eligible (p1 already has DRAFT row).
        result2 = tasks.auto_comment_next()
        assert result2["action"] == "skip"
        assert result2["reason"] == "no_eligible"

    def test_live_mode_unchanged_when_dry_run_off(self, monkeypatch, db):
        """Default env (no AUTO_COMMENT_DRY_RUN) → behaves exactly like K-3."""
        from bot import tasks

        # Explicitly NOT setting AUTO_COMMENT_DRY_RUN.
        monkeypatch.delenv("AUTO_COMMENT_DRY_RUN", raising=False)

        _seed_account(db)
        post = _seed_post(db)
        _patch_common(monkeypatch, db)
        _stub_ai_draft(monkeypatch, draft_text="live komen real")

        from bot.modules.comment_sender import SendResult

        send_called = MagicMock()

        async def _ok(**kwargs):
            send_called(**kwargs)
            return SendResult(
                success=True,
                comment_text=kwargs.get("comment_text", ""),
                post_url=kwargs.get("post_url", ""),
                fb_comment_id="fb_real_999",
                error=None,
            )

        monkeypatch.setattr("bot.tasks.send_comment", _ok)

        result = tasks.auto_comment_next()

        # Live path: send_comment fired, SENT row, post=COMMENTED.
        send_called.assert_called_once()
        assert result["action"] == "sent"

        rows = db.query(CommentHistory).all()
        assert len(rows) == 1
        assert rows[0].status == "SENT"
        assert rows[0].fb_comment_id == "fb_real_999"

        db.refresh(post)
        assert post.status == "COMMENTED"

    def test_dry_run_off_explicit_zero_runs_live(self, monkeypatch, db):
        """AUTO_COMMENT_DRY_RUN=0 explicit also goes live."""
        from bot import tasks

        monkeypatch.setenv("AUTO_COMMENT_DRY_RUN", "0")

        _seed_account(db)
        _seed_post(db)
        _patch_common(monkeypatch, db)
        _stub_ai_draft(monkeypatch)

        from bot.modules.comment_sender import SendResult

        async def _ok(**kwargs):
            return SendResult(
                success=True,
                comment_text=kwargs.get("comment_text", ""),
                post_url=kwargs.get("post_url", ""),
                fb_comment_id="fb_real_111",
                error=None,
            )

        monkeypatch.setattr("bot.tasks.send_comment", _ok)

        result = tasks.auto_comment_next()

        assert result["action"] == "sent"
        rows = db.query(CommentHistory).all()
        assert rows[0].status == "SENT"
