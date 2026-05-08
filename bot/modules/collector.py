"""Collector — fetches posts from Facebook targets."""

import logging
from typing import Any

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def collect_all_targets(self):
    """Celery task: collect posts from all active targets."""
    # TODO: Load targets from config
    # TODO: For each target, check rate_guard and circuit_breaker
    # TODO: Fetch posts (API or Playwright)
    # TODO: Parse and normalize
    # TODO: Save to database
    logger.info("collect_all_targets triggered")
    pass


class Collector:
    """Fetch posts from Facebook targets."""

    def __init__(self, rate_guard, circuit_breaker, config: dict[str, Any]):
        self.rate_guard = rate_guard
        self.circuit_breaker = circuit_breaker
        self.config = config

    async def collect_target(self, target: dict[str, Any]) -> list[dict[str, Any]]:
        """Collect posts from a single target."""
        target_id = target["id"]

        if not self.circuit_breaker.is_available(target_id):
            logger.warning("Target %s is suspended, skipping", target_id)
            return []

        if not self.rate_guard.check_and_reserve(target_id):
            logger.info("Target %s rate limited, skipping", target_id)
            return []

        mode = target.get("mode", "scrape_public")

        try:
            if mode == "api_first":
                posts = await self._collect_via_api(target)
            else:
                posts = await self._collect_via_scrape(target)

            self.circuit_breaker.record_success(target_id)
            return posts

        except Exception as e:
            status = self.circuit_breaker.record_failure(target_id)
            logger.error(
                "Collection failed for %s (status: %s): %s",
                target_id, status, e
            )
            return []

    async def _collect_via_api(self, target: dict[str, Any]) -> list[dict[str, Any]]:
        """Collect via Facebook Graph API."""
        # TODO: Implement Graph API collection
        logger.info("API collection for %s (not implemented)", target["id"])
        return []

    async def _collect_via_scrape(self, target: dict[str, Any]) -> list[dict[str, Any]]:
        """Collect via Playwright scraping."""
        # TODO: Implement Playwright scraping
        logger.info("Scrape collection for %s (not implemented)", target["id"])
        return []
