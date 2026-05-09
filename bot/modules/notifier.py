"""Notifier — Telegram alerts and periodic reports.

Safety rails added in Phase E:

* **Markdown escape.** All user-provided interpolations (target ids,
  error text, signals) are escaped for Telegram's ``MarkdownV2`` dialect
  before being formatted into a message. The old ``Markdown`` mode would
  drop the entire message when an id contained an unescaped ``_``.
* **Dedup + rate limit.** ``_should_send(key)`` keeps a TTL map keyed by
  ``(alert_type, target_id)`` so the same block-detected signal cannot
  spam Telegram on every collector tick.
* **Exponential retry.** 429 / 5xx responses are retried with a bounded
  backoff so transient outages don't silently drop alerts.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"

# MarkdownV2 reserved characters — see Telegram Bot API docs.
_MDV2_ESCAPES = r"_*[]()~`>#+-=|{}.!"
_MDV2_ESCAPE_RE = re.compile(f"([{re.escape(_MDV2_ESCAPES)}])")

DEFAULT_DEDUP_TTL_SECONDS = 15 * 60  # 15 minutes
DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_BACKOFF = 1.5


def escape_markdown_v2(text: str) -> str:
    """Escape a string for Telegram ``MarkdownV2`` formatting."""
    if text is None:
        return ""
    return _MDV2_ESCAPE_RE.sub(r"\\\1", str(text))


class Notifier:
    """Send notifications via Telegram with dedup + retry."""

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        dedup_ttl_seconds: int = DEFAULT_DEDUP_TTL_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self.enabled = bool(self.bot_token and self.chat_id)

        self._dedup_ttl = int(dedup_ttl_seconds)
        self._max_retries = int(max_retries)
        self._dedup: dict[str, float] = {}
        self._dedup_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Dedup helpers
    # ------------------------------------------------------------------
    def _should_send(self, key: str | None) -> bool:
        """Return True when ``key`` has not been seen within the TTL.

        ``key=None`` bypasses dedup entirely — use that for one-off
        reports (daily/weekly) where we always want delivery.
        """
        if key is None:
            return True
        now = time.time()
        with self._dedup_lock:
            # Expire stale entries.
            expired = [k for k, ts in self._dedup.items() if now - ts > self._dedup_ttl]
            for k in expired:
                self._dedup.pop(k, None)
            if key in self._dedup:
                return False
            self._dedup[key] = now
            return True

    def clear_dedup(self) -> None:
        with self._dedup_lock:
            self._dedup.clear()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def send_alert(
        self, message: str, level: str = "info", dedup_key: str | None = None
    ) -> bool:
        """Send an alert. ``message`` is treated as already-escaped MDV2."""
        if not self.enabled:
            logger.debug("Telegram notifications disabled, skipping alert")
            return False
        if not self._should_send(dedup_key):
            logger.debug("Telegram alert suppressed by dedup: %s", dedup_key)
            return False

        prefix = {
            "info": "ℹ️",
            "warning": "⚠️",
            "error": "🚨",
            "success": "✅",
        }.get(level, "📌")
        body = f"{prefix} *FB Bot Alert*\n\n{message}"
        return await self._send_message(body)

    async def send_daily_summary(self, stats: dict[str, Any]) -> bool:
        e = escape_markdown_v2
        lines = [
            "📊 *Daily Summary*",
            "",
            f"📥 Posts collected: {e(stats.get('posts_collected', 0))}",
            f"📋 Posts queued: {e(stats.get('posts_queued', 0))}",
            f"✏️ Drafts created: {e(stats.get('drafts_created', 0))}",
            f"✅ Drafts approved: {e(stats.get('drafts_approved', 0))}",
            f"❌ Drafts rejected: {e(stats.get('drafts_rejected', 0))}",
            f"🚨 Errors: {e(stats.get('errors', 0))}",
            "",
            f"🎯 Targets active: {e(stats.get('targets_active', 0))}",
            f"⚠️ Targets degraded: {e(stats.get('targets_degraded', 0))}",
        ]
        return await self._send_message("\n".join(lines))

    async def send_weekly_report(self, stats: dict[str, Any]) -> bool:
        e = escape_markdown_v2
        approval = "{:.1f}%".format(stats.get("approval_rate", 0))
        edit = "{:.1f}%".format(stats.get("edit_rate", 0))
        reject = "{:.1f}%".format(stats.get("reject_rate", 0))
        lines = [
            "📈 *Weekly Report*",
            "",
            f"📥 Total posts: {e(stats.get('total_posts', 0))}",
            f"✏️ Total drafts: {e(stats.get('total_drafts', 0))}",
            f"✅ Approval rate: {e(approval)}",
            f"📝 Edit rate: {e(edit)}",
            f"❌ Reject rate: {e(reject)}",
            "",
            f"🏆 Best target: {e(stats.get('best_target', 'N/A'))}",
            f"⚡ Best hour: {e(stats.get('best_hour', 'N/A'))}",
            f"🤖 AI drafts: {e(stats.get('ai_drafts', 0))}",
        ]
        return await self._send_message("\n".join(lines))

    async def notify_block_detected(self, target_id: str, signal: str) -> bool:
        if not self._should_send(f"block:{target_id}"):
            return False
        e = escape_markdown_v2
        message = (
            "🚫 *Block Detected*\n\n"
            f"Target: `{e(target_id)}`\n"
            f"Signal: {e(signal)}\n\n"
            "Target suspended by circuit breaker\\."
        )
        return await self._send_message(message)

    async def notify_collection_error(self, target_id: str, error: str) -> bool:
        if not self._should_send(f"error:{target_id}:{error[:40]}"):
            return False
        e = escape_markdown_v2
        message = (
            "⚠️ *Collection Error*\n\n"
            f"Target: `{e(target_id)}`\n"
            f"Error: {e(error)}"
        )
        return await self._send_message(message)

    async def notify_service_health(
        self, service: str, status: str, detail: str = ""
    ) -> bool:
        if not self._should_send(f"health:{service}:{status}"):
            return False
        e = escape_markdown_v2
        emoji = "✅" if status == "healthy" else "🚨"
        message = (
            f"{emoji} *Service Health*\n\n"
            f"Service: `{e(service)}`\n"
            f"Status: {e(status)}"
        )
        if detail:
            message += f"\nDetail: {e(detail)}"
        return await self._send_message(message)

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------
    async def _send_message(self, text: str) -> bool:
        """POST to Telegram with bounded exponential backoff."""
        if not self.enabled:
            return False
        url = TELEGRAM_API_URL.format(token=self.bot_token)
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": True,
        }

        backoff = DEFAULT_BASE_BACKOFF
        for attempt in range(1, self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(url, json=payload)
                if response.status_code == 200:
                    logger.info("Telegram message sent successfully")
                    return True
                if response.status_code in (429, 500, 502, 503, 504):
                    logger.warning(
                        "Telegram transient error %d (attempt %d/%d): %s",
                        response.status_code,
                        attempt,
                        self._max_retries,
                        response.text[:200],
                    )
                else:
                    logger.error(
                        "Telegram API error %d: %s",
                        response.status_code,
                        response.text[:200],
                    )
                    return False
            except httpx.TimeoutException:
                logger.warning(
                    "Telegram send timeout (attempt %d/%d)",
                    attempt,
                    self._max_retries,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("Telegram send failed: %s", exc)
                return False

            if attempt < self._max_retries:
                await asyncio.sleep(backoff)
                backoff *= 2

        return False
