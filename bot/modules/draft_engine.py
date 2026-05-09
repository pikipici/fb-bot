"""Draft Response Engine — generates draft comments with fallback chain."""

import hashlib
import json
import random
import re
from pathlib import Path
from typing import Any


class DraftEngine:
    """Generate draft responses with fallback: AI -> semi-dynamic -> static."""

    def __init__(self, templates_path: str | None = None, ai_prompts_path: str | None = None):
        config_dir = Path(__file__).parent.parent / "config"

        if templates_path is None:
            templates_path = str(config_dir / "response_templates.json")
        if ai_prompts_path is None:
            ai_prompts_path = str(config_dir / "ai_prompts.json")

        with open(templates_path) as f:
            self.templates = json.load(f)["templates"]
        with open(ai_prompts_path) as f:
            self.ai_config = json.load(f)

        self._draft_fingerprints: set[str] = set()
        self._forbidden_phrases = [
            p.lower()
            for p in self.ai_config.get("brand_guidelines", {}).get("forbidden_phrases", [])
        ]
        self._max_length = 300

    def generate_draft(self, post: dict[str, Any], ai_enabled: bool = False) -> dict[str, Any]:
        """Generate a draft response using fallback chain."""
        # Try AI draft first (if enabled)
        if ai_enabled:
            draft = self._try_ai_draft(post)
            if draft and self._validate_draft(draft["text"]):
                draft["source_type"] = "ai"
                return draft

        # Try semi-dynamic template
        draft = self._try_semi_dynamic(post)
        if draft and self._validate_draft(draft["text"]):
            draft["source_type"] = "semi_dynamic"
            return draft

        # Try static template
        draft = self._try_static(post)
        if draft and self._validate_draft(draft["text"]):
            draft["source_type"] = "static"
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
        """Attempt AI-generated draft. Placeholder for LLM integration."""
        # TODO: Integrate with AI provider (OpenAI/Ollama)
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
        """Validate draft against safety rules."""
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

        # Check fingerprint uniqueness
        fingerprint = self._compute_fingerprint(text)
        if fingerprint in self._draft_fingerprints:
            return False
        self._draft_fingerprints.add(fingerprint)

        return True

    def _compute_fingerprint(self, text: str) -> str:
        """Compute MD5 fingerprint of draft text."""
        return hashlib.md5(text.strip().lower().encode()).hexdigest()

    def reset_fingerprints(self):
        """Reset fingerprint cache (e.g. between runs)."""
        self._draft_fingerprints.clear()
