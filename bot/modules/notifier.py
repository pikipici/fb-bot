"""Notifier — Telegram alerts and periodic reports."""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


class Notifier:
    """Send notifications via Telegram."""

    def __init__(self):
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self.enabled = bool(self.bot_token and self.chat_id)

    async def send_alert(self, message: str, level: str = "info"):
        """Send an alert message to Telegram."""
        if not self.enabled:
            logger.debug("Telegram notifications disabled, skipping alert")
            return

        prefix = {"info": "ℹ️", "warning": "⚠️", "error": "🚨", "success": "✅"}.get(
            level, "📌"
        )
        full_message = f"{prefix} *FB Bot Alert*\n\n{message}"

        # TODO: Implement actual Telegram API call via python-telegram-bot
        logger.info("Would send Telegram alert: %s", full_message)

    async def send_daily_summary(self, stats: dict[str, Any]):
        """Send daily summary report."""
        lines = [
            "📊 *Daily Summary*",
            f"Posts collected: {stats.get('posts_collected', 0)}",
            f"Posts queued: {stats.get('posts_queued', 0)}",
            f"Drafts created: {stats.get('drafts_created', 0)}",
            f"Drafts approved: {stats.get('drafts_approved', 0)}",
            f"Errors: {stats.get('errors', 0)}",
        ]
        await self.send_alert("\n".join(lines), level="info")
