"""AI Draft Service — LLM-backed comment draft generator.

Calls an OpenAI-compatible endpoint (sumopod.com by default) to generate a
conversational Indonesian comment that references the post's context and
uses the active comment templates as a *style* reference (not a verbatim
copy).

Env vars::

    SUMOPOD_API_KEY   # required
    SUMOPOD_BASE_URL  # default: https://ai.sumopod.com/v1
    AI_DRAFT_MODEL    # default: MiniMax-M2.7-highspeed

Rate limiting: in-memory per-user cooldown, 15 seconds. Enough for single-
user MVP; a multi-user deployment should move this into the DB.

Usage::

    svc = AIDraftService(db)
    text = svc.generate(post_id=post.id, user_id=current_user.id)
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

import httpx
from sqlalchemy.orm import Session

from server.models import CommentTemplate, TrendingPost

logger = logging.getLogger(__name__)


# --- constants --------------------------------------------------------------

DEFAULT_BASE_URL = "https://ai.sumopod.com/v1"
DEFAULT_MODEL = "MiniMax-M2.7-highspeed"

COOLDOWN_SECONDS = 15
"""Per-user cooldown between AI draft requests."""

MAX_OUTPUT_CHARS = 500
"""Hard cap on returned draft length. Long outputs get truncated."""

MAX_TOKENS = 512
"""Upper bound for LLM completion tokens."""

HTTP_TIMEOUT_S = 30.0
"""HTTPX timeout per LLM call."""


# --- errors -----------------------------------------------------------------


class AIDraftServiceError(Exception):
    """Base class for AI draft service errors."""


class AIDraftConfigError(AIDraftServiceError):
    """Raised when required env vars are missing."""


class AIDraftNotFoundError(AIDraftServiceError):
    """Raised when the referenced TrendingPost does not exist."""


class AIDraftRateLimitError(AIDraftServiceError):
    """Raised when the per-user cooldown hasn't elapsed."""


class AIDraftUpstreamError(AIDraftServiceError):
    """Raised when the LLM API returned an error. Message is sanitized."""


class AIDraftEmptyResponseError(AIDraftServiceError):
    """Raised when the LLM returned empty / whitespace-only content."""


# --- rate limit store (module-level so all service instances share it) ------

_rate_lock = threading.Lock()
_last_call_by_user: dict[int, float] = {}


def _reset_rate_limit_for_tests() -> None:
    """Test helper. Not part of the public API."""
    with _rate_lock:
        _last_call_by_user.clear()


# --- service ----------------------------------------------------------------


class AIDraftService:
    """Generate a contextual comment draft via LLM."""

    def __init__(self, db: Session) -> None:
        self.db = db
        api_key = os.getenv("SUMOPOD_API_KEY", "").strip()
        if not api_key:
            raise AIDraftConfigError(
                "SUMOPOD_API_KEY is required for AI draft generation"
            )
        self._api_key = api_key
        self._base_url = os.getenv("SUMOPOD_BASE_URL", DEFAULT_BASE_URL).strip()
        self._model = os.getenv("AI_DRAFT_MODEL", DEFAULT_MODEL).strip()

    # --- public API ---------------------------------------------------------

    def generate(self, *, post_id: int, user_id: int) -> str:
        """Build prompt, call LLM, return a clean draft text.

        Raises:
            AIDraftNotFoundError: post_id not in DB.
            AIDraftRateLimitError: user called again within COOLDOWN_SECONDS.
            AIDraftUpstreamError: LLM API failed.
            AIDraftEmptyResponseError: LLM returned empty text.
        """
        self._enforce_rate_limit(user_id)

        post = (
            self.db.query(TrendingPost)
            .filter(TrendingPost.id == post_id)
            .first()
        )
        if post is None:
            raise AIDraftNotFoundError(f"post {post_id} not found")

        templates = self._load_active_templates()
        messages = self._build_messages(post, templates)
        raw = self._call_llm(messages)
        return self._postprocess(raw)

    # --- internals ----------------------------------------------------------

    def _enforce_rate_limit(self, user_id: int) -> None:
        now = time.monotonic()
        with _rate_lock:
            last = _last_call_by_user.get(user_id)
            if last is not None and (now - last) < COOLDOWN_SECONDS:
                wait = int(COOLDOWN_SECONDS - (now - last)) + 1
                raise AIDraftRateLimitError(
                    f"tunggu {wait}s bro, cooldown {COOLDOWN_SECONDS}s per user"
                )
            _last_call_by_user[user_id] = now

    def _load_active_templates(self) -> list[str]:
        rows = (
            self.db.query(CommentTemplate)
            .filter(CommentTemplate.is_active.is_(True))
            .order_by(CommentTemplate.updated_at.desc())
            .limit(5)
            .all()
        )
        return [r.template_text for r in rows if r.template_text]

    def _build_messages(
        self, post: TrendingPost, templates: list[str]
    ) -> list[dict[str, str]]:
        system = (
            "Lu komentator Facebook casual berbahasa Indonesia. "
            "Tulis SATU komen pendek (1-2 kalimat, max 250 karakter) "
            "yang nyambung ke konteks post di bawah. "
            "Gaya santai, natural, jangan formal, jangan spam. "
            "Kalau template style disediakan, pake nada + CTA-nya sebagai "
            "inspirasi TAPI JANGAN copy-paste — susun paragraf baru yang "
            "menggabungkan konteks post dengan pesan promosi dari template. "
            "Output HANYA kalimat komen itu sendiri — tanpa quote, tanpa "
            "prefix 'Komen:', tanpa emoji berlebihan."
        )

        user_parts: list[str] = ["POST:"]
        if post.author_name:
            user_parts.append(f"Author: {post.author_name}")
        if post.text_snippet:
            user_parts.append(f'Text: "{post.text_snippet}"')
        metrics = []
        if post.likes:
            metrics.append(f"{post.likes} likes")
        if post.comments:
            metrics.append(f"{post.comments} comments")
        if post.shares:
            metrics.append(f"{post.shares} shares")
        if metrics:
            user_parts.append("Engagement: " + ", ".join(metrics))

        if templates:
            user_parts.append("")
            user_parts.append("STYLE TEMPLATES (referensi tone, JANGAN copy):")
            for i, t in enumerate(templates, 1):
                user_parts.append(f"{i}. {t}")

        user_parts.append("")
        user_parts.append("Komen baru:")

        return [
            {"role": "system", "content": system},
            {"role": "user", "content": "\n".join(user_parts)},
        ]

    def _call_llm(self, messages: list[dict[str, str]]) -> str:
        url = f"{self._base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": MAX_TOKENS,
            "temperature": 0.9,
            "top_p": 0.95,
        }

        try:
            with httpx.Client(timeout=HTTP_TIMEOUT_S) as client:
                resp = client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            # Log full detail for devs, raise sanitized error to caller.
            logger.warning("AI draft upstream failed: %s", exc, exc_info=True)
            raise AIDraftUpstreamError("LLM provider gagal, coba lagi sebentar")

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            logger.warning("AI draft unexpected response shape: %s", data)
            raise AIDraftUpstreamError("LLM provider balikin format aneh")

        return content or ""

    def _postprocess(self, raw: str) -> str:
        text = (raw or "").strip()
        if not text:
            raise AIDraftEmptyResponseError("LLM balikin empty response")

        # Strip matching wrapping quotes LLMs sometimes add.
        for pair in (('"', '"'), ("'", "'"), ("`", "`")):
            if text.startswith(pair[0]) and text.endswith(pair[1]) and len(text) > 1:
                text = text[1:-1].strip()

        # Strip common "Komen:" prefix.
        for prefix in ("Komen:", "Komentar:", "Comment:"):
            if text.lower().startswith(prefix.lower()):
                text = text[len(prefix):].strip()

        if len(text) > MAX_OUTPUT_CHARS:
            text = text[:MAX_OUTPUT_CHARS].rstrip()

        if not text:
            raise AIDraftEmptyResponseError("LLM balikin empty response (post-process)")

        return text
