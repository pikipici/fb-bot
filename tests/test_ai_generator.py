"""Tests for AI Generator module."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

import httpx

from bot.modules.ai_generator import (
    AIGenerator,
    PROVIDER_OPENAI,
    PROVIDER_OLLAMA,
)


@pytest.fixture
def ai_config():
    return {
        "system": {
            "id": "default_id",
            "text": "Kamu adalah asisten engagement yang ramah dan tidak hard-selling.",
        },
        "per_language": {
            "id": {"tone": "santai-sopan", "max_length": 240},
            "en": {"tone": "friendly-professional", "max_length": 220},
        },
        "brand_guidelines": {
            "forbidden_phrases": ["dijamin", "100% pasti", "hasil instan"],
            "preferred_phrases": ["boleh diskusi dulu", "semoga membantu"],
        },
    }


@pytest.fixture
def sample_post():
    return {
        "fb_post_id": "123",
        "text_snippet": "Butuh jasa desain logo untuk usaha kecil",
        "language": "id",
        "likes": 15,
        "comments": 8,
        "shares": 2,
        "detected_keywords": ["jasa", "desain"],
    }


@pytest.fixture
def generator(ai_config):
    return AIGenerator(
        ai_config=ai_config,
        provider="openai",
        model="gpt-4o-mini",
        api_key="test-key",
    )


@pytest.fixture
def ollama_generator(ai_config):
    return AIGenerator(
        ai_config=ai_config,
        provider="ollama",
        model="llama3",
        base_url="http://localhost:11434",
    )


class TestBuildPrompt:
    def test_basic_prompt_id(self, generator, sample_post):
        prompt = generator.build_prompt(sample_post)
        assert "system" in prompt
        assert "user" in prompt
        assert "asisten engagement" in prompt["system"]
        assert "240 karakter" in prompt["user"]
        assert "santai-sopan" in prompt["user"]
        assert "Butuh jasa desain logo" in prompt["user"]

    def test_prompt_english(self, generator):
        post = {
            "text_snippet": "Looking for a web developer",
            "language": "en",
            "likes": 5,
            "comments": 2,
            "shares": 0,
        }
        prompt = generator.build_prompt(post)
        assert "220 karakter" in prompt["user"]
        assert "friendly-professional" in prompt["user"]

    def test_prompt_includes_forbidden_phrases(self, generator, sample_post):
        prompt = generator.build_prompt(sample_post)
        assert "dijamin" in prompt["user"]
        assert "100% pasti" in prompt["user"]

    def test_prompt_includes_preferred_phrases(self, generator, sample_post):
        prompt = generator.build_prompt(sample_post)
        assert "boleh diskusi dulu" in prompt["user"]

    def test_prompt_includes_engagement(self, generator, sample_post):
        prompt = generator.build_prompt(sample_post)
        assert "15 likes" in prompt["user"]
        assert "8 comments" in prompt["user"]

    def test_prompt_includes_category(self, generator, sample_post):
        prompt = generator.build_prompt(sample_post)
        assert "jasa" in prompt["user"]
        assert "desain" in prompt["user"]

    def test_prompt_default_category_when_no_keywords(self, generator):
        post = {"text_snippet": "Hello", "language": "id", "likes": 0, "comments": 0, "shares": 0}
        prompt = generator.build_prompt(post)
        assert "umum" in prompt["user"]

    def test_prompt_unknown_language_defaults(self, generator):
        post = {"text_snippet": "Hola", "language": "es", "likes": 0, "comments": 0, "shares": 0}
        prompt = generator.build_prompt(post)
        # Should still produce a prompt, just with empty tone/length defaults
        assert "user" in prompt


class TestGenerateOpenAI:
    @pytest.mark.asyncio
    async def test_successful_generation(self, generator, sample_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [
                {"message": {"content": "Halo, boleh diskusi dulu soal kebutuhan desainnya?"}}
            ]
        }

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            result = await generator.generate(sample_post)

        assert result is not None
        assert "diskusi" in result

    @pytest.mark.asyncio
    async def test_empty_choices(self, generator, sample_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"choices": []}

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            result = await generator.generate(sample_post)

        assert result is None

    @pytest.mark.asyncio
    async def test_timeout_retries(self, generator, sample_post):
        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise httpx.TimeoutException("timeout")

        with patch("httpx.AsyncClient.post", side_effect=mock_post):
            result = await generator.generate(sample_post)

        assert result is None
        # Should retry: 1 initial + 2 retries = 3
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_auth_error_no_retry(self, generator, sample_post):
        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            response = MagicMock()
            response.status_code = 401
            raise httpx.HTTPStatusError("unauthorized", request=MagicMock(), response=response)

        with patch("httpx.AsyncClient.post", side_effect=mock_post):
            result = await generator.generate(sample_post)

        assert result is None
        assert call_count == 1  # No retry on 401

    @pytest.mark.asyncio
    async def test_server_error_retries(self, generator, sample_post):
        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            response = MagicMock()
            response.status_code = 500
            raise httpx.HTTPStatusError("server error", request=MagicMock(), response=response)

        with patch("httpx.AsyncClient.post", side_effect=mock_post):
            result = await generator.generate(sample_post)

        assert result is None
        assert call_count == 3  # Retries on 500


class TestGenerateOllama:
    @pytest.mark.asyncio
    async def test_successful_ollama(self, ollama_generator, sample_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "message": {"content": "Semoga membantu ya, bisa DM kalau mau diskusi lebih lanjut."}
        }

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            result = await ollama_generator.generate(sample_post)

        assert result is not None
        assert "membantu" in result

    @pytest.mark.asyncio
    async def test_ollama_empty_response(self, ollama_generator, sample_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"message": {"content": ""}}

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            result = await ollama_generator.generate(sample_post)

        assert result is None


class TestProviderConfig:
    def test_default_openai_provider(self, ai_config):
        gen = AIGenerator(ai_config=ai_config, api_key="k")
        assert gen.provider == "openai"
        assert gen.model == "gpt-4o-mini"
        assert "openai.com" in gen.base_url

    def test_ollama_provider(self, ai_config):
        gen = AIGenerator(ai_config=ai_config, provider="ollama")
        assert gen.provider == "ollama"
        assert gen.model == "llama3"
        assert "11434" in gen.base_url

    def test_env_override(self, ai_config, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER", "ollama")
        monkeypatch.setenv("AI_MODEL", "mistral")
        monkeypatch.setenv("AI_BASE_URL", "http://custom:8080")

        gen = AIGenerator(ai_config=ai_config)
        assert gen.provider == "ollama"
        assert gen.model == "mistral"
        assert gen.base_url == "http://custom:8080"

    def test_explicit_params_override_env(self, ai_config, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER", "ollama")
        gen = AIGenerator(ai_config=ai_config, provider="openai", model="gpt-4o", api_key="x")
        assert gen.provider == "openai"
        assert gen.model == "gpt-4o"


class TestDraftEngineAIIntegration:
    """Test DraftEngine with AI generator wired in."""

    def test_ai_draft_used_when_enabled(self):
        """AI draft should be used when ai_enabled=True and generator returns valid text."""
        from bot.modules.draft_engine import DraftEngine

        mock_gen = MagicMock()
        mock_gen.generate = AsyncMock(return_value="Boleh diskusi dulu soal kebutuhannya?")

        engine = DraftEngine(ai_generator=mock_gen)
        result = engine.generate_draft(
            {"id": 1, "text": "butuh jasa", "language": "id"},
            ai_enabled=True,
        )

        assert result["source_type"] == "ai"
        assert "diskusi" in result["text"]

    def test_ai_draft_fallback_on_failure(self):
        """Should fallback to template when AI returns None."""
        from bot.modules.draft_engine import DraftEngine

        mock_gen = MagicMock()
        mock_gen.generate = AsyncMock(return_value=None)

        engine = DraftEngine(ai_generator=mock_gen)
        result = engine.generate_draft(
            {"id": 1, "text": "butuh jasa desain", "language": "id"},
            ai_enabled=True,
        )

        # Should fallback to semi_dynamic or static
        assert result["source_type"] in ("semi_dynamic", "static", "manual")

    def test_ai_draft_fallback_on_validation_fail(self):
        """Should fallback when AI returns text that fails validation (too long)."""
        from bot.modules.draft_engine import DraftEngine

        mock_gen = MagicMock()
        # Return text that's too long (>300 chars)
        mock_gen.generate = AsyncMock(return_value="x" * 400)

        engine = DraftEngine(ai_generator=mock_gen)
        result = engine.generate_draft(
            {"id": 1, "text": "butuh jasa desain", "language": "id"},
            ai_enabled=True,
        )

        assert result["source_type"] != "ai"

    def test_ai_draft_fallback_on_forbidden_phrase(self):
        """Should fallback when AI returns text with forbidden phrase."""
        from bot.modules.draft_engine import DraftEngine

        mock_gen = MagicMock()
        mock_gen.generate = AsyncMock(return_value="Dijamin hasilnya bagus!")

        engine = DraftEngine(ai_generator=mock_gen)
        result = engine.generate_draft(
            {"id": 1, "text": "butuh jasa", "language": "id"},
            ai_enabled=True,
        )

        assert result["source_type"] != "ai"

    def test_no_ai_generator_skips_ai(self):
        """When no AI generator configured, should skip to templates."""
        from bot.modules.draft_engine import DraftEngine

        engine = DraftEngine(ai_generator=None)
        result = engine.generate_draft(
            {"id": 1, "text": "butuh jasa desain", "language": "id"},
            ai_enabled=True,
        )

        assert result["source_type"] in ("semi_dynamic", "static", "manual")
