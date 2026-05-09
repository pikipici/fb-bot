"""Notifier — Telegram alerts and periodic reports."""

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


class Notifier:
    """Send notifications via Telegram."""

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
    ):
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self.enabled = bool(self.bot_token and self.chat_id)

    async def send_alert(self, message: str, level: str = "info") -> bool:
        """Send an alert message to Telegram.

        Args:
            message: Alert text (supports Markdown).
            level: One of 'info', 'warning', 'error', 'success'.

        Returns:
            True if sent successfully, False otherwise.
        """
        if not self.enabled:
            logger.debug("Telegram notifications disabled, skipping alert")
            return False

        prefix = {
            "info": "ℹ️",
            "warning": "⚠️",
            "error": "🚨",
            "success": "✅",
        }.get(level, "📌")

        full_message = f"{prefix} *FB Bot Alert*\n\n{message}"
        return await self._send_message(full_message)

    async def send_daily_summary(self, stats: dict[str, Any]) -> bool:
        """Send daily summary report."""
        lines = [
            "📊 *Daily Summary*",
            "",
            f"📥 Posts collected: {stats.get('posts_collected', 0)}",
            f"📋 Posts queued: {stats.get('posts_queued', 0)}",
            f"✏️ Drafts created: {stats.get('drafts_created', 0)}",
            f"✅ Drafts approved: {stats.get('drafts_approved', 0)}",
            f"❌ Drafts rejected: {stats.get('drafts_rejected', 0)}",
            f"🚨 Errors: {stats.get('errors', 0)}",
            "",
            f"🎯 Targets active: {stats.get('targets_active', 0)}",
            f"⚠️ Targets degraded: {stats.get('targets_degraded', 0)}",
        ]
        return await self._send_message("\n".join(lines))

    async def send_weekly_report(self, stats: dict[str, Any]) -> bool:
        """Send weekly report."""
        lines = [
            "📈 *Weekly Report*",
            "",
            f"📥 Total posts: {stats.get('total_posts', 0)}",
            f"✏️ Total drafts: {stats.get('total_drafts', 0)}",
            f"✅ Approval rate: {stats.get('approval_rate', 0):.1f}%",
            f"📝 Edit rate: {stats.get('edit_rate', 0):.1f}%",
            f"❌ Reject rate: {stats.get('reject_rate', 0):.1f}%",
            "",
            f"🏆 Best target: {stats.get('best_target', 'N/A')}",
            f"⚡ Best hour: {stats.get('best_hour', 'N/A')}",
            f"🤖 AI drafts: {stats.get('ai_drafts', 0)}",
        ]
        return await self._send_message("\n".join(lines))

    async def notify_block_detected(self, target_id: str, signal: str) -> bool:
        """Alert when a target gets blocked/captcha'd."""
        message = (
            f"🚫 *Block Detected*\n\n"
            f"Target: `{target_id}`\n"
            f"Signal: {signal}\n\n"
            f"Target suspended by circuit breaker."
        )
        return await self._send_message(message)

    async def notify_collection_error(self, target_id: str, error: str) -> bool:
        """Alert on repeated collection failures."""
        message = (
            f"⚠️ *Collection Error*\n\n"
            f"Target: `{target_id}`\n"
            f"Error: {error}"
        )
        return await self._send_message(message)

    async def notify_service_health(self, service: str, status: str, detail: str = "") -> bool:
        """Alert on service health changes."""
        emoji = "✅" if status == "healthy" else "🚨"
        message = (
            f"{emoji} *Service Health*\n\n"
            f"Service: `{service}`\n"
            f"Status: {status}"
        )
        if detail:
            message += f"\nDetail: {detail}"
        return await self._send_message(message)

    async def _send_message(self, text: str) -> bool:
        """Send message via Telegram Bot API."""
        url = TELEGRAM_API_URL.format(token=self.bot_token)
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, json=payload)
                if response.status_code == 200:
                    logger.info("Telegram message sent successfully")
                    return True
                else:
                    logger.warning(
                        "Telegram API error %d: %s",
                        response.status_code,
                        response.text[:200],
                    )
                    return False
        except httpx.TimeoutException:
            logger.error("Telegram send timeout")
            return False
        except Exception as e:
            logger.error("Telegram send failed: %s", e)
            return False
