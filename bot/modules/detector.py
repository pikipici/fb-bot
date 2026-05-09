"""Detector — keyword matching, risk tagging, language filter, duplicate check."""

import json
from pathlib import Path
from typing import Any


class Detector:
    """Filter and tag posts based on keywords, risk, language, and duplicates."""

    def __init__(
        self,
        keywords_path: str | None = None,
        blacklist_path: str | None = None,
    ):
        config_dir = Path(__file__).parent.parent / "config"

        if keywords_path is None:
            keywords_path = str(config_dir / "keywords.json")
        if blacklist_path is None:
            blacklist_path = str(config_dir / "blacklist.json")

        with open(keywords_path) as f:
            kw_config = json.load(f)
        with open(blacklist_path) as f:
            self.blacklist = json.load(f)

        self.whitelist = [w.lower() for w in kw_config.get("whitelist", [])]
        self.blacklist_phrases = [b.lower() for b in self.blacklist.get("phrases", [])]
        self.supported_languages = kw_config.get("languages", ["id", "en"])
        self.default_language = kw_config.get("language", "id")

        self._seen_ids: set[str] = set()

    def detect(self, post: dict[str, Any]) -> dict[str, Any]:
        """Run all detection checks on a post. Returns enriched post dict."""
        post = post.copy()

        # Language filter
        post["language"] = post.get("language", self.default_language)
        post["language_ok"] = post["language"] in self.supported_languages

        # Keyword matching
        matched = self._match_keywords(post.get("text", ""))
        post["matched_keywords"] = matched["count"]
        post["matched_keyword_list"] = matched["keywords"]
        post["total_keywords"] = len(self.whitelist)

        # Risk tagging
        post["risk_tags"] = self._detect_risk(post.get("text", ""))

        # Duplicate check
        fb_post_id = post.get("fb_post_id", "")
        post["is_duplicate"] = fb_post_id in self._seen_ids
        if fb_post_id:
            self._seen_ids.add(fb_post_id)

        return post

    def should_filter_out(self, post: dict[str, Any], max_age_hours: float = 48) -> tuple[bool, str]:
        """Determine if post should be filtered out. Returns (filtered, reason)."""
        # Language check
        if not post.get("language_ok", True):
            return True, "unsupported_language"

        # Duplicate check
        if post.get("is_duplicate", False):
            return True, "duplicate"

        # No keyword match at all
        if post.get("matched_keywords", 0) == 0 and not post.get("risk_tags"):
            return True, "no_keyword_match"

        # High risk (3+ risk tags)
        risk_tags = post.get("risk_tags", [])
        if len(risk_tags) >= 3:
            return True, "high_risk"

        return False, ""

    def _match_keywords(self, text: str) -> dict[str, Any]:
        """Match whitelist keywords against post text."""
        text_lower = text.lower()
        matched = [kw for kw in self.whitelist if kw in text_lower]
        return {"count": len(matched), "keywords": matched}

    def _detect_risk(self, text: str) -> list[str]:
        """Detect risk phrases in post text."""
        text_lower = text.lower()
        return [phrase for phrase in self.blacklist_phrases if phrase in text_lower]

    def add_seen_id(self, fb_post_id: str):
        """Manually add an ID to the seen set (e.g. from DB)."""
        self._seen_ids.add(fb_post_id)

    def load_seen_ids(self, ids: list[str]):
        """Bulk load seen IDs from database."""
        self._seen_ids.update(ids)
