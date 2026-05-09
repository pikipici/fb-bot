"""Draft Response Engine — generates draft comments with fallback chain.

Thread-safety:
* ``_draft_fingerprints`` and its mutations are protected by a
  threading.Lock so the set stays consistent when ``_try_ai_draft``
  runs in a worker thread (via the async→sync bridge).
* Fingerprint side-effects are split: ``_is_novel_fingerprint`` only
  reports, ``_register_fingerprint`` mutates. Validation no longer
  poisons the cache with drafts that are ultimately discarded.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import logging
import random
import re
import threading
from pathlib import Path
from typing import Any

from bot.modules.ai_generator import AIGenerator

logger = logging.getLogger(__name__)


class DraftEngine:
    """Generate draft responses with fallback: AI -> semi-dynamic -> static."""

    def __init__(
        self,
        templates_path: str | None = None,
        ai_prompts_path: str | None = None,
        ai_generator: AIGenerator | None = None,
    ):
        config_dir = Path(__file__).parent.parent / "config"

        if templates_path is None:
            templates_path = str(config_dir / "response_templates.json")
        if ai_prompts_path is None:
            ai_prompts_path = str(config_dir / "ai_prompts.json")

        with open(templates_path) as f:
            self.templates = json.load(f)["templates"]
        with open(ai_prompts_path) as f:
            self.ai_config = json.load(f)

        self._ai_generator = ai_generator
        self._draft_fingerprints: set[str] = set()
        self._fingerprint_lock = threading.Lock()
        self._forbidden_phrases = [
            p.lower()
            for p in self.ai_config.get("brand_guidelines", {}).get("forbidden_phrases", [])
        ]
        self._max_length = 300

    def generate_draft(self, post: dict[str, Any], ai_enabled: bool = False) -> dict[str, Any]:
        """Generate a draft response using fallback chain.

        Each attempt is only *registered* in the fingerprint set after
        it is accepted as the final draft — so speculative AI attempts
        that get rejected for length / link / duplicate reasons don't
        block later template fallbacks.
        """
        # Try AI draft first (if enabled)
        if ai_enabled:
            draft = self._try_ai_draft(post)
            if draft and self._validate_draft(draft["text"]):
                draft["source_type"] = "ai"
                self._register_fingerprint(draft["fingerprint"])
                return draft

        # Try semi-dynamic template
        draft = self._try_semi_dynamic(post)
        if draft and self._validate_draft(draft["text"]):
            draft["source_type"] = "semi_dynamic"
            self._register_fingerprint(draft["fingerprint"])
            return draft

        # Try static template
        draft = self._try_static(post)
        if draft and self._validate_draft(draft["text"]):
            draft["source_type"] = "static"
            self._register_fingerprint(draft["fingerprint"])
            return draft

        # All paths failed
        return {
            "text": None,
            "source_type": "manual",
            "status": "NEEDS_MANUAL_WRITE",
            "post_id": post.get("id"),
            "template_id": None,
            "fingerprint": None,
        }

    def _try_ai_draft(self, post: dict[str, Any]) -> dict[str, Any] | None:
        """Attempt AI-generated draft via LLM provider.

        The async bridge uses a dedicated ``ThreadPoolExecutor`` with
        ``max_workers=1``. If the submitted future times out we cancel
        it and log — the worker thread may continue briefly but the
        pool is explicitly shut down with ``wait=False`` so the pool
        itself does not block exit.
        """
        if not self._ai_generator:
            logger.debug("No AI generator configured, skipping AI draft")
            return None

        try:
            loop = None
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                pass

            if loop and loop.is_running():
                pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                future = pool.submit(asyncio.run, self._ai_generator.generate(post))
                try:
                    text = future.result(timeout=60)
                finally:
                    future.cancel()
                    pool.shutdown(wait=False)
            else:
                text = asyncio.run(self._ai_generator.generate(post))

            if not text:
                logger.info("AI generator returned empty result")
                return None

            fingerprint = self._compute_fingerprint(text)
            return {
                "text": text,
                "template_id": None,
                "post_id": post.get("id"),
                "status": "PENDING_REVIEW",
                "fingerprint": fingerprint,
            }

        except concurrent.futures.TimeoutError:
            logger.warning("AI draft generation timed out")
            return None
        except Exception as e:  # noqa: BLE001
            logger.warning("AI draft generation failed: %s", e)
            return None

    def _try_semi_dynamic(self, post: dict[str, Any]) -> dict[str, Any] | None:
        """Try to match a semi-dynamic template based on keywords."""
        post_text = post.get("text", "").lower()
        language = post.get("language", "id")

        # Collect all matching templates
        matches = []
        for template in self.templates.get("semi_dynamic", []):
            if template.get("language") != language:
                continue
            for keyword in template.get("trigger_keywords", []):
                if keyword.lower() in post_text:
                    matches.append(template)
                    break

        if not matches:
            return None

        # Randomize selection for variety
        template = random.choice(matches)
        text = template["template"].replace(
            "{{category}}", template.get("category", "")
        )
        fingerprint = self._compute_fingerprint(text)

        return {
            "text": text,
            "template_id": template["id"],
            "post_id": post.get("id"),
            "status": "PENDING_REVIEW",
            "fingerprint": fingerprint,
        }

    def _try_static(self, post: dict[str, Any]) -> dict[str, Any] | None:
        """Pick a static template matching the post language."""
        language = post.get("language", "id")

        # Collect matching templates
        matches = [
            t for t in self.templates.get("static", [])
            if t.get("language") == language
        ]

        if not matches:
            return None

        # Randomize selection
        template = random.choice(matches)
        fingerprint = self._compute_fingerprint(template["text"])

        return {
            "text": template["text"],
            "template_id": template["id"],
            "post_id": post.get("id"),
            "status": "PENDING_REVIEW",
            "fingerprint": fingerprint,
        }

    def _validate_draft(self, text: str) -> bool:
        """Validate draft against safety rules.

        This method is side-effect free — callers must explicitly call
        :meth:`_register_fingerprint` after they commit to using the draft.
        """
        if not text:
            return False

        # Check length
        if len(text) > self._max_length:
            return False

        # Check forbidden phrases
        text_lower = text.lower()
        for phrase in self._forbidden_phrases:
            if phrase in text_lower:
                return False

        # Check for links
        if re.search(r"https?://", text):
            return False

        # Check fingerprint uniqueness (read-only)
        fingerprint = self._compute_fingerprint(text)
        with self._fingerprint_lock:
            if fingerprint in self._draft_fingerprints:
                return False
        return True

    def _register_fingerprint(self, fingerprint: str | None) -> None:
        """Record a fingerprint as used. Idempotent. Thread-safe."""
        if not fingerprint:
            return
        with self._fingerprint_lock:
            self._draft_fingerprints.add(fingerprint)

    def _compute_fingerprint(self, text: str) -> str:
        """Compute a short hex digest of draft text used for dedup.

        SHA-256 replaces the previous MD5 to keep bandit/pylint happy;
        we only use the first 32 hex chars so the column width (64)
        stays identical.
        """
        digest = hashlib.sha256(text.strip().lower().encode()).hexdigest()
        return digest[:32]

    def reset_fingerprints(self) -> None:
        """Reset fingerprint cache (e.g. between runs)."""
        with self._fingerprint_lock:
            self._draft_fingerprints.clear()
