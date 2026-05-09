"""AI Draft Generator — LLM-powered draft comment generation."""

import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Supported providers
PROVIDER_OPENAI = "openai"
PROVIDER_OLLAMA = "ollama"

# Defaults
DEFAULT_PROVIDER = PROVIDER_OPENAI
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_OLLAMA_MODEL = "llama3"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 2


class AIGenerator:
    """Generate draft comments using LLM providers (OpenAI or Ollama)."""

    def __init__(
        self,
        ai_config: dict[str, Any] | None = None,
        config_path: str | Path | None = None,
        provider: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        """Initialize AI generator.

        Args:
            ai_config: Loaded ai_prompts.json dict. If None, loads from config_path.
            config_path: Path to ai_prompts.json (fallback if ai_config not given).
            provider: 'openai' or 'ollama'. Defaults to env AI_PROVIDER or 'openai'.
            model: Model name. Defaults to env AI_MODEL or provider default.
            api_key: API key for OpenAI. Defaults to env OPENAI_API_KEY.
            base_url: Base URL override (for Ollama or OpenAI-compatible APIs).
            timeout: Request timeout in seconds.
            max_retries: Max retry attempts on failure.
        """
        # Load AI config
        if ai_config:
            self.ai_config = ai_config
        elif config_path:
            self.ai_config = self._load_config(config_path)
        else:
            default_path = Path(__file__).parent.parent / "config" / "ai_prompts.json"
            self.ai_config = self._load_config(default_path)

        # Provider settings
        self.provider = provider or os.getenv("AI_PROVIDER", DEFAULT_PROVIDER)
        self.model = model or os.getenv("AI_MODEL", self._default_model())
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.base_url = base_url or os.getenv("AI_BASE_URL", self._default_base_url())
        self.timeout = timeout
        self.max_retries = max_retries

    def _default_model(self) -> str:
        if self.provider == PROVIDER_OLLAMA:
            return DEFAULT_OLLAMA_MODEL
        return DEFAULT_OPENAI_MODEL

    def _default_base_url(self) -> str:
        if self.provider == PROVIDER_OLLAMA:
            return DEFAULT_OLLAMA_URL
        return "https://api.openai.com"

    def _load_config(self, path: str | Path) -> dict[str, Any]:
        path = Path(path)
        if not path.exists():
            logger.warning("AI config not found: %s", path)
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def build_prompt(self, post: dict[str, Any]) -> dict[str, str]:
        """Build system + user prompt from config and post context.

        Returns dict with 'system' and 'user' keys.
        """
        language = post.get("language", "id")
        lang_config = self.ai_config.get("per_language", {}).get(language, {})
        tone = lang_config.get("tone", "santai-sopan")
        max_length = lang_config.get("max_length", 240)

        system_text = self.ai_config.get("system", {}).get("text", "")

        # Build engagement summary
        likes = post.get("likes", 0)
        comments = post.get("comments", 0)
        shares = post.get("shares", 0)
        engagement = f"{likes} likes, {comments} comments, {shares} shares"

        # Detected category from pipeline
        detected_keywords = post.get("detected_keywords", [])
        category = ", ".join(detected_keywords[:3]) if detected_keywords else "umum"

        # Brand guidelines
        brand = self.ai_config.get("brand_guidelines", {})
        forbidden = brand.get("forbidden_phrases", [])
        preferred = brand.get("preferred_phrases", [])

        user_prompt = (
            f"Berikan satu draft balasan singkat (maks {max_length} karakter) "
            f"untuk komentar Facebook berikut:\n\n"
            f"--- Postingan ---\n"
            f"Teks: {post.get('text_snippet', post.get('text', ''))}\n"
            f"Bahasa: {language}\n"
            f"Engagement: {engagement}\n"
            f"Kategori: {category}\n\n"
            f"Aturan:\n"
            f"- Jangan menyertakan link.\n"
            f"- Hindari klaim berlebihan (garansi, hasil pasti, dsb.).\n"
            f"- Nada: {tone}.\n"
            f"- Ajak user untuk lanjut via pesan jika relevan, tapi tanpa memaksa.\n"
            f"- JANGAN gunakan frasa: {', '.join(forbidden)}.\n"
            f"- Boleh gunakan frasa: {', '.join(preferred)}.\n"
            f"- Jawab HANYA dengan teks draft, tanpa penjelasan tambahan."
        )

        return {"system": system_text, "user": user_prompt}

    async def generate(self, post: dict[str, Any]) -> str | None:
        """Generate AI draft for a post.

        Returns draft text string, or None if generation fails.
        """
        prompt = self.build_prompt(post)

        for attempt in range(self.max_retries + 1):
            try:
                if self.provider == PROVIDER_OLLAMA:
                    result = await self._call_ollama(prompt)
                else:
                    result = await self._call_openai(prompt)

                if result:
                    return result.strip()

            except httpx.TimeoutException:
                logger.warning(
                    "AI generation timeout (attempt %d/%d)",
                    attempt + 1, self.max_retries + 1,
                )
            except httpx.HTTPStatusError as e:
                logger.error(
                    "AI API error %d (attempt %d/%d): %s",
                    e.response.status_code, attempt + 1, self.max_retries + 1, e,
                )
                # Don't retry on auth errors
                if e.response.status_code in (401, 403):
                    break
            except Exception as e:
                logger.error(
                    "AI generation error (attempt %d/%d): %s",
                    attempt + 1, self.max_retries + 1, e,
                )

        return None

    async def _call_openai(self, prompt: dict[str, str]) -> str | None:
        """Call OpenAI-compatible chat completions API."""
        url = f"{self.base_url}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": prompt["system"]},
                {"role": "user", "content": prompt["user"]},
            ],
            "max_tokens": 300,
            "temperature": 0.7,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

        choices = data.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "")
        return None

    async def _call_ollama(self, prompt: dict[str, str]) -> str | None:
        """Call Ollama generate API."""
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": prompt["system"]},
                {"role": "user", "content": prompt["user"]},
            ],
            "stream": False,
            "options": {
                "temperature": 0.7,
                "num_predict": 300,
            },
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()

        return data.get("message", {}).get("content", "")
