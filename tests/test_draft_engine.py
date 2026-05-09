"""Tests for draft engine."""

import pytest

from bot.modules.draft_engine import DraftEngine


@pytest.fixture
def engine():
    e = DraftEngine()
    e.reset_fingerprints()
    return e


class TestFallbackChain:
    def test_semi_dynamic_match(self, engine):
        post = {"id": 1, "text": "Saya cari jasa desain logo", "language": "id"}
        result = engine.generate_draft(post)
        assert result["source_type"] == "semi_dynamic"
        assert result["status"] == "PENDING_REVIEW"
        assert result["text"] is not None

    def test_static_fallback_no_keyword(self, engine):
        post = {"id": 2, "text": "Halo semua selamat pagi", "language": "id"}
        result = engine.generate_draft(post)
        # No keyword match → falls to static
        assert result["source_type"] == "static"
        assert result["status"] == "PENDING_REVIEW"

    def test_english_static_template(self, engine):
        post = {"id": 3, "text": "Hello everyone good morning", "language": "en"}
        result = engine.generate_draft(post)
        assert result["source_type"] == "static"
        assert result["text"] is not None
        assert result["status"] == "PENDING_REVIEW"

    def test_needs_manual_when_no_template(self, engine):
        post = {"id": 4, "text": "Bonjour tout le monde", "language": "fr"}
        result = engine.generate_draft(post)
        assert result["source_type"] == "manual"
        assert result["status"] == "NEEDS_MANUAL_WRITE"
        assert result["text"] is None


class TestValidator:
    def test_rejects_long_text(self, engine):
        # Force a long template by manipulating
        result = engine._validate_draft("x" * 301)
        assert result is False

    def test_rejects_forbidden_phrase(self, engine):
        result = engine._validate_draft("Ini dijamin berhasil 100%")
        assert result is False

    def test_rejects_links(self, engine):
        result = engine._validate_draft("Cek di https://example.com ya")
        assert result is False

    def test_accepts_valid_text(self, engine):
        result = engine._validate_draft("Halo kak, semoga membantu ya.")
        assert result is True

    def test_rejects_empty(self, engine):
        result = engine._validate_draft("")
        assert result is False


class TestFingerprint:
    def test_duplicate_fingerprint_rejected(self, engine):
        text = "Halo kak, boleh diskusi dulu."
        assert engine._validate_draft(text) is True
        # Same text again should be rejected
        assert engine._validate_draft(text) is False

    def test_reset_fingerprints(self, engine):
        text = "Halo kak, boleh diskusi dulu."
        engine._validate_draft(text)
        engine.reset_fingerprints()
        # After reset, same text should pass
        assert engine._validate_draft(text) is True

    def test_fingerprint_computed(self, engine):
        fp = engine._compute_fingerprint("Hello World")
        assert isinstance(fp, str)
        assert len(fp) == 32  # SHA-256 truncated to 32 hex chars


class TestDraftOutput:
    def test_draft_has_required_fields(self, engine):
        post = {"id": 10, "text": "Butuh jasa editing video", "language": "id"}
        result = engine.generate_draft(post)
        assert "text" in result
        assert "source_type" in result
        assert "status" in result
        assert "post_id" in result
        assert "fingerprint" in result

    def test_multiple_drafts_vary(self, engine):
        """Multiple calls should potentially pick different templates."""
        post = {"id": 11, "text": "Cari jasa dan butuh desain", "language": "id"}
        results = set()
        for _ in range(20):
            engine.reset_fingerprints()
            result = engine.generate_draft(post)
            if result["template_id"]:
                results.add(result["template_id"])
        # With multiple matching templates, we should see variety
        # (at least 1 template, possibly more if config has multiple)
        assert len(results) >= 1
