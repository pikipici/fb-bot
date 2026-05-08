"""Recovery Manager — handles error recovery and state snapshots."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class RecoveryManager:
    """Manage error recovery, snapshots, and watchdog state."""

    def __init__(self, data_dir: str | None = None):
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "data")
        self.data_dir = Path(data_dir)
        self.snapshot_path = self.data_dir / "last_snapshot.json"

    def save_snapshot(self, state: dict[str, Any]):
        """Save current batch state for resume capability."""
        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "state": state,
        }
        self.snapshot_path.write_text(json.dumps(snapshot, indent=2))
        logger.info("Snapshot saved at %s", snapshot["timestamp"])

    def load_snapshot(self) -> dict[str, Any] | None:
        """Load last saved snapshot for recovery."""
        if not self.snapshot_path.exists():
            return None
        try:
            data = json.loads(self.snapshot_path.read_text())
            logger.info("Snapshot loaded from %s", data.get("timestamp"))
            return data.get("state")
        except (json.JSONDecodeError, KeyError) as e:
            logger.error("Failed to load snapshot: %s", e)
            return None

    def should_alert_stale(self, last_activity: datetime, timeout_minutes: int = 30) -> bool:
        """Check if system is stale (no activity within timeout)."""
        now = datetime.now(timezone.utc)
        elapsed = (now - last_activity).total_seconds() / 60
        return elapsed > timeout_minutes
