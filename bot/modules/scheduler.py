"""Scheduler — selects and prioritizes targets for collection."""

import json
import logging
import time
from pathlib import Path
from typing import Any

from bot.modules.circuit_breaker import CircuitBreaker
from bot.modules.rate_guard import RateGuard

logger = logging.getLogger(__name__)


class TargetScheduler:
    """Load targets from config and determine which ones to collect."""

    def __init__(
        self,
        config_path: str | Path | None = None,
        targets: list[dict[str, Any]] | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        rate_guard: RateGuard | None = None,
    ):
        """Initialize scheduler.

        Args:
            config_path: Path to targets.json (used if targets not provided).
            targets: Direct list of target dicts (overrides config_path).
            circuit_breaker: CircuitBreaker instance for health checks.
            rate_guard: RateGuard instance for rate limit checks.
        """
        self.circuit_breaker = circuit_breaker or CircuitBreaker()
        self.rate_guard = rate_guard or RateGuard({})
        self._last_run: dict[str, float] = {}

        if targets is not None:
            self._targets = targets
        elif config_path:
            self._targets = self._load_targets(config_path)
        else:
            self._targets = []

    def _load_targets(self, config_path: str | Path) -> list[dict[str, Any]]:
        """Load targets from JSON config file."""
        path = Path(config_path)
        if not path.exists():
            logger.warning("Targets config not found: %s", path)
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("targets", [])
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to load targets config: %s", e)
            return []

    def get_runnable_targets(self) -> list[dict[str, Any]]:
        """Get list of targets that are eligible to run now.

        Filters:
        - enabled == True
        - circuit breaker allows (not SUSPENDED)
        - cooldown elapsed since last run

        Returns targets sorted by priority (highest first).
        """
        now = time.time()
        runnable = []

        for target in self._targets:
            target_id = target.get("id", "")

            # Skip disabled
            if not target.get("enabled", True):
                logger.debug("Target %s disabled, skipping", target_id)
                continue

            # Check circuit breaker
            if not self.circuit_breaker.is_available(target_id):
                logger.info("Target %s suspended by circuit breaker", target_id)
                continue

            # Check cooldown
            cooldown_minutes = target.get("cooldown_minutes", 30)
            last_run = self._last_run.get(target_id, 0)
            elapsed_minutes = (now - last_run) / 60

            if last_run > 0 and elapsed_minutes < cooldown_minutes:
                logger.debug(
                    "Target %s in cooldown (%.1f/%.1f min)",
                    target_id, elapsed_minutes, cooldown_minutes,
                )
                continue

            runnable.append(target)

        # Sort by priority descending
        runnable.sort(key=lambda t: t.get("priority", 0), reverse=True)
        return runnable

    def mark_run(self, target_id: str):
        """Mark a target as having been run now."""
        self._last_run[target_id] = time.time()

    def mark_run_at(self, target_id: str, timestamp: float):
        """Mark a target as having been run at a specific time."""
        self._last_run[target_id] = timestamp

    def get_target_by_id(self, target_id: str) -> dict[str, Any] | None:
        """Get a specific target by ID."""
        for target in self._targets:
            if target.get("id") == target_id:
                return target
        return None

    def reload_targets(self, config_path: str | Path):
        """Reload targets from config file."""
        self._targets = self._load_targets(config_path)
        logger.info("Reloaded %d targets from config", len(self._targets))

    @property
    def all_targets(self) -> list[dict[str, Any]]:
        """Get all targets regardless of status."""
        return self._targets.copy()
