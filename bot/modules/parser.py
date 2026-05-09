"""Parser — extract and normalize post metadata from raw data."""

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Regex patterns for FB post extraction
FB_POST_ID_PATTERN = re.compile(r'/posts/(\d+)|/permalink/(\d+)|story_fbid=(\d+)')
ENGAGEMENT_NUMBER_PATTERN = re.compile(r'([\d,.]+)\s*[KkMm]?')


class Parser:
    """Parse raw collected data into normalized post dicts."""

    def parse_scraped_posts(self, raw_posts: list[dict[str, Any]], target: dict[str, Any]) -> list[dict[str, Any]]:
        """Parse a list of raw scraped post data into normalized format.

        Args:
            raw_posts: List of raw post dicts from scraper (HTML-extracted fields).
            target: Target config dict for context.

        Returns:
            List of normalized post dicts ready for pipeline.
        """
        results = []
        for raw in raw_posts:
            try:
                parsed = self._normalize_post(raw, target)
                if parsed:
                    results.append(parsed)
            except Exception as e:
                logger.warning("Failed to parse post: %s", e)
                continue
        return results

    def parse_api_posts(self, api_response: dict[str, Any], target: dict[str, Any]) -> list[dict[str, Any]]:
        """Parse Graph API response into normalized format.

        Args:
            api_response: Raw API response dict with 'data' key.
            target: Target config dict for context.

        Returns:
            List of normalized post dicts.
        """
        posts_data = api_response.get("data", [])
        results = []
        for item in posts_data:
            try:
                parsed = self._normalize_api_post(item, target)
                if parsed:
                    results.append(parsed)
            except Exception as e:
                logger.warning("Failed to parse API post: %s", e)
                continue
        return results

    def _normalize_post(self, raw: dict[str, Any], target: dict[str, Any]) -> dict[str, Any] | None:
        """Normalize a single scraped post.

        Required: at least text_snippet or fb_post_id must be present.
        """
        text = raw.get("text", "").strip()
        fb_post_id = raw.get("fb_post_id", "")

        # Try to extract post ID from URL if not provided
        if not fb_post_id and raw.get("url"):
            fb_post_id = self._extract_post_id(raw["url"])

        # Generate synthetic ID if none found
        if not fb_post_id and text:
            fb_post_id = self._generate_synthetic_id(text, target.get("id", ""))

        # Must have at least an ID
        if not fb_post_id:
            logger.debug("Skipping post with no ID and no text")
            return None

        return {
            "fb_post_id": fb_post_id,
            "url": raw.get("url", ""),
            "author_name": raw.get("author_name", ""),
            "author_id": raw.get("author_id", ""),
            "text_snippet": text[:500] if text else "",
            "timestamp": self._parse_timestamp(raw.get("timestamp")),
            "likes": self._parse_engagement_number(raw.get("likes", 0)),
            "comments": self._parse_engagement_number(raw.get("comments", 0)),
            "shares": self._parse_engagement_number(raw.get("shares", 0)),
            "language": raw.get("language", "id"),
            "target_id": target.get("id", ""),
            "target_name": target.get("name", ""),
            "source_mode": "scrape",
        }

    def _normalize_api_post(self, item: dict[str, Any], target: dict[str, Any]) -> dict[str, Any] | None:
        """Normalize a single Graph API post."""
        fb_post_id = item.get("id", "")
        if not fb_post_id:
            return None

        message = item.get("message", "")
        created_time = item.get("created_time", "")

        # Parse engagement from reactions/comments/shares
        reactions = item.get("reactions", {}).get("summary", {}).get("total_count", 0)
        comments_count = item.get("comments", {}).get("summary", {}).get("total_count", 0)
        shares_count = item.get("shares", {}).get("count", 0)

        return {
            "fb_post_id": fb_post_id,
            "url": f"https://www.facebook.com/{fb_post_id}",
            "author_name": item.get("from", {}).get("name", ""),
            "author_id": item.get("from", {}).get("id", ""),
            "text_snippet": message[:500] if message else "",
            "timestamp": self._parse_iso_timestamp(created_time),
            "likes": reactions,
            "comments": comments_count,
            "shares": shares_count,
            "language": self._detect_language(message),
            "target_id": target.get("id", ""),
            "target_name": target.get("name", ""),
            "source_mode": "api",
        }

    def _extract_post_id(self, url: str) -> str:
        """Extract Facebook post ID from URL."""
        match = FB_POST_ID_PATTERN.search(url)
        if match:
            return next(g for g in match.groups() if g is not None)
        return ""

    def _generate_synthetic_id(self, text: str, target_id: str) -> str:
        """Generate a synthetic post ID from content hash."""
        content = f"{target_id}:{text[:200]}"
        return f"syn_{hashlib.md5(content.encode()).hexdigest()[:12]}"

    def _parse_timestamp(self, raw_ts: Any) -> str:
        """Parse various timestamp formats to ISO string."""
        if not raw_ts:
            return datetime.now(timezone.utc).isoformat()

        if isinstance(raw_ts, datetime):
            return raw_ts.isoformat()

        if isinstance(raw_ts, str):
            # Try ISO format first
            try:
                return datetime.fromisoformat(raw_ts).isoformat()
            except ValueError:
                pass

            # Try common FB relative formats
            raw_lower = raw_ts.lower()
            now = datetime.now(timezone.utc)

            if "just now" in raw_lower or "baru saja" in raw_lower:
                return now.isoformat()
            if "min" in raw_lower or "menit" in raw_lower:
                minutes = self._extract_first_number(raw_ts)
                from datetime import timedelta
                return (now - timedelta(minutes=minutes)).isoformat()
            if "hour" in raw_lower or "jam" in raw_lower:
                hours = self._extract_first_number(raw_ts)
                from datetime import timedelta
                return (now - timedelta(hours=hours)).isoformat()
            if "day" in raw_lower or "hari" in raw_lower:
                days = self._extract_first_number(raw_ts)
                from datetime import timedelta
                return (now - timedelta(days=days)).isoformat()

        return datetime.now(timezone.utc).isoformat()

    def _parse_iso_timestamp(self, ts_str: str) -> str:
        """Parse ISO 8601 timestamp from API."""
        if not ts_str:
            return datetime.now(timezone.utc).isoformat()
        try:
            return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).isoformat()
        except ValueError:
            return datetime.now(timezone.utc).isoformat()

    def _parse_engagement_number(self, value: Any) -> int:
        """Parse engagement numbers with locale-agnostic separator handling.

        Facebook renders engagement numbers in the viewer's locale. In
        ``en-US`` a decimal is ``1.2K`` and a thousands separator is
        ``1,500``. In ``id-ID`` / most EU locales this is inverted:
        ``1,2K`` is the decimal form and ``1.500`` is the thousands form.
        The collector pins its viewport locale to ``id-ID`` but that is
        not a hard guarantee for every template, so we accept both.

        Algorithm:
        1. Extract and remove any ``K``/``M`` suffix, recording the
           multiplier.
        2. If the remainder contains both ``.`` and ``,`` — the
           right-most separator is the decimal marker, and every
           occurrence of the other separator is a thousands grouping
           that we strip.
        3. If only a single separator is present — treat it as the
           decimal marker when followed by 1 or 2 digits, otherwise as
           a thousands grouping.
        4. Parse the cleaned string as float and apply the multiplier.
        """
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if not isinstance(value, str):
            return 0

        value = value.strip()
        if not value:
            return 0

        # 1. Strip K/M suffix.
        multiplier = 1
        if value.lower().endswith("k"):
            multiplier = 1000
            value = value[:-1].rstrip()
        elif value.lower().endswith("m"):
            multiplier = 1_000_000
            value = value[:-1].rstrip()

        has_dot = "." in value
        has_comma = "," in value

        if has_dot and has_comma:
            # Mixed separators — the last-seen one wins as decimal.
            last_dot = value.rfind(".")
            last_comma = value.rfind(",")
            if last_dot > last_comma:
                # '.' is decimal, ',' is thousands grouping.
                value = value.replace(",", "")
            else:
                # ',' is decimal, '.' is thousands grouping.
                value = value.replace(".", "").replace(",", ".")
        elif has_comma:
            # Only ',' — decimal when followed by 1-2 digits, else thousands.
            decimals = len(value.split(",")[-1])
            if 1 <= decimals <= 2:
                value = value.replace(",", ".")
            else:
                value = value.replace(",", "")
        elif has_dot:
            # Only '.' — decimal when followed by 1-2 digits, else thousands.
            decimals = len(value.split(".")[-1])
            if decimals >= 3:
                value = value.replace(".", "")
            # else: already a decimal literal; leave alone.

        try:
            return int(float(value) * multiplier)
        except (ValueError, TypeError):
            return 0

    def _extract_first_number(self, text: str) -> int:
        """Extract first number from text."""
        match = re.search(r'\d+', text)
        return int(match.group()) if match else 1

    def _detect_language(self, text: str) -> str:
        """Simple language detection based on common words."""
        if not text:
            return "id"

        text_lower = text.lower()
        id_markers = ["yang", "dan", "untuk", "dengan", "dari", "ini", "itu", "ada", "bisa", "juga"]
        en_markers = ["the", "and", "for", "with", "from", "this", "that", "have", "can", "also"]

        id_count = sum(1 for w in id_markers if f" {w} " in f" {text_lower} ")
        en_count = sum(1 for w in en_markers if f" {w} " in f" {text_lower} ")

        if en_count > id_count:
            return "en"
        return "id"
