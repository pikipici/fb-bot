"""Tests for AI Draft Service — LLM-backed comment draft generation.

Covers:
* Happy path: build prompt from post + active templates, call LLM, return text.
* Rate limit: per-user 15s window, raises AIDraftRateLimitError on 2nd call.
* Missing post: raises AIDraftNotFoundError.
* No active templates: still works (uses generic system prompt fallback).
* LLM API error: raises AIDraftUpstreamError (generic, non-leaking).
* LLM returns empty: raises AIDraftEmptyResponseError.
* Env vars missing: raises AIDraftConfigError at service build time.

Mocks httpx.Client so no real network call.
"""
from __future__ import annotations

import time
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.database import Base
from server.models import CommentTemplate, Source, TrendingPost
from server.services.ai_draft_service import (
    AIDraftConfigError,
    AIDraftEmptyResponseError,
    AIDraftNotFoundError,
    AIDraftRateLimitError,
    AIDraftService,
    AIDraftUpstreamError,
    _reset_rate_limit_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_rate_limit_between_tests():
    """Module-level rate limit dict must not leak across tests."""
    _reset_rate_limit_for_tests()
    yield
    _reset_rate_limit_for_tests()


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path}/test_ai_draft.db",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    try:
        source = Source(
            type="home_feed", label="Test Home"
        )
        db.add(source)
        db.commit()
        db.refresh(source)

        post = TrendingPost(
            fb_post_id="fb_123",
            source_id=source.id,
            author_name="Interserver",
            text_snippet="Say Goodbye to Limits! Get unlimited storage & email hosting.",
            post_url="https://facebook.com/posts/123",
            likes=210_000,
            comments=16_200,
            shares=1_900,
            score=228_100.0,
            status="NEW",
        )
        db.add(post)

        template = CommentTemplate(
            name="promo-hosting",
            template_text="Cek juga digimarket.id — hosting murah, gratis SSL!",
            is_active=True,
        )
        db.add(template)
        db.commit()

        yield db
    finally:
        db.close()


def _mock_llm_response(content: str) -> MagicMock:
    """Build a mock httpx.Response with OpenAI-compatible JSON."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"content": content}}],
    }
    resp.raise_for_status = MagicMock()
    return resp


@pytest.fixture
def svc_factory(db_session, monkeypatch):
    """Build an AIDraftService with env vars set + mock httpx."""
    monkeypatch.setenv("SUMOPOD_API_KEY", "test-key")
    monkeypatch.setenv("SUMOPOD_BASE_URL", "https://ai.sumopod.com/v1")
    monkeypatch.setenv("AI_DRAFT_MODEL", "MiniMax-M2.7-highspeed")

    def _build():
        return AIDraftService(db_session)

    return _build


class TestHappyPath:
    def test_returns_generated_text(self, svc_factory):
        svc = svc_factory()
        mock_resp = _mock_llm_response("Keren bro, gue juga pake hosting murah di digimarket.id!")
        with patch("httpx.Client.post", return_value=mock_resp) as mock_post:
            text = svc.generate(post_id=1, user_id=42)
            assert "digimarket" in text.lower() or "hosting" in text.lower() or len(text) > 10
            assert mock_post.called
            call_kwargs = mock_post.call_args.kwargs
            payload = call_kwargs["json"]
            # Prompt must mention the post author + text
            prompt_blob = str(payload)
            assert "Interserver" in prompt_blob
            assert "Say Goodbye" in prompt_blob
            # And the active template as style reference
            assert "digimarket.id" in prompt_blob

    def test_no_active_templates_still_works(self, svc_factory, db_session):
        # deactivate the template
        db_session.query(CommentTemplate).update({"is_active": False})
        db_session.commit()
        svc = svc_factory()
        mock_resp = _mock_llm_response("Mantap bro keren!")
        with patch("httpx.Client.post", return_value=mock_resp):
            text = svc.generate(post_id=1, user_id=42)
            assert text.strip() == "Mantap bro keren!"


class TestRateLimit:
    def test_second_call_within_window_blocks(self, svc_factory):
        svc = svc_factory()
        mock_resp = _mock_llm_response("ok")
        with patch("httpx.Client.post", return_value=mock_resp):
            svc.generate(post_id=1, user_id=42)
            with pytest.raises(AIDraftRateLimitError) as exc:
                svc.generate(post_id=1, user_id=42)
            assert "15" in str(exc.value) or "tunggu" in str(exc.value).lower()

    def test_different_users_dont_share_window(self, svc_factory):
        svc = svc_factory()
        mock_resp = _mock_llm_response("ok")
        with patch("httpx.Client.post", return_value=mock_resp):
            svc.generate(post_id=1, user_id=1)
            # different user: should NOT be blocked
            svc.generate(post_id=1, user_id=2)

    def test_after_window_allows_again(self, svc_factory, monkeypatch):
        svc = svc_factory()
        mock_resp = _mock_llm_response("ok")
        with patch("httpx.Client.post", return_value=mock_resp):
            svc.generate(post_id=1, user_id=42)
            # Advance time past the 15s window by monkeypatching time.monotonic
            fake_now = [time.monotonic() + 20]
            monkeypatch.setattr(
                "server.services.ai_draft_service.time.monotonic",
                lambda: fake_now[0],
            )
            svc.generate(post_id=1, user_id=42)  # should not raise


class TestErrors:
    def test_missing_post_raises_not_found(self, svc_factory):
        svc = svc_factory()
        with pytest.raises(AIDraftNotFoundError):
            svc.generate(post_id=9999, user_id=42)

    def test_llm_http_error_wrapped(self, svc_factory):
        svc = svc_factory()
        err_resp = MagicMock()
        err_resp.status_code = 500
        err_resp.text = "internal upstream error with sensitive details"
        err_resp.raise_for_status.side_effect = Exception("upstream 500")
        with patch("httpx.Client.post", return_value=err_resp):
            with pytest.raises(AIDraftUpstreamError) as exc:
                svc.generate(post_id=1, user_id=42)
            # Error message must NOT leak raw upstream response
            assert "sensitive details" not in str(exc.value)

    def test_empty_llm_response_raises(self, svc_factory):
        svc = svc_factory()
        mock_resp = _mock_llm_response("")
        with patch("httpx.Client.post", return_value=mock_resp):
            with pytest.raises(AIDraftEmptyResponseError):
                svc.generate(post_id=1, user_id=42)

    def test_whitespace_only_response_raises(self, svc_factory):
        svc = svc_factory()
        mock_resp = _mock_llm_response("   \n\n  ")
        with patch("httpx.Client.post", return_value=mock_resp):
            with pytest.raises(AIDraftEmptyResponseError):
                svc.generate(post_id=1, user_id=42)

    def test_missing_api_key_raises_config_error(self, db_session, monkeypatch):
        monkeypatch.delenv("SUMOPOD_API_KEY", raising=False)
        with pytest.raises(AIDraftConfigError):
            AIDraftService(db_session)


class TestPromptShape:
    def test_prompt_strips_quotes_from_response(self, svc_factory):
        svc = svc_factory()
        # Some LLMs wrap output in quotes
        mock_resp = _mock_llm_response('"Mantap bro, gue juga suka!"')
        with patch("httpx.Client.post", return_value=mock_resp):
            text = svc.generate(post_id=1, user_id=42)
            assert not text.startswith('"')
            assert not text.endswith('"')

    def test_prompt_truncates_very_long_response(self, svc_factory):
        svc = svc_factory()
        huge = "a" * 2000
        mock_resp = _mock_llm_response(huge)
        with patch("httpx.Client.post", return_value=mock_resp):
            text = svc.generate(post_id=1, user_id=42)
            # Hard cap at 500 chars to stay under FB typical comment limit
            assert len(text) <= 500
