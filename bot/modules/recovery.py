"""Recovery Manager — handles error recovery and state snapshots.

Writes are atomic: we stage JSON to a sibling ``.tmp`` file and then
``os.replace`` it into place. POSIX guarantees the rename is atomic on
the same filesystem, so a crash mid-write cannot leave a truncated
snapshot on disk. A write failure is logged and propagated so the
caller knows recovery is not persisted.
"""

from __future__ import annotations

import json
import logging
import os
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

    def save_snapshot(self, state: dict[str, Any]) -> None:
        """Save current batch state for resume capability.

        Uses a tmp-file + ``os.replace`` dance so partial writes are
        impossible. Raises ``OSError`` to the caller if persistence
        fails — recovery state is worth surfacing, not silently
        swallowing.
        """
        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "state": state,
        }
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.error("Cannot create snapshot dir %s: %s", self.data_dir, exc)
            raise

        tmp_path = self.snapshot_path.with_suffix(self.snapshot_path.suffix + ".tmp")
        try:
            payload = json.dumps(snapshot, indent=2)
            tmp_path.write_text(payload, encoding="utf-8")
            os.replace(tmp_path, self.snapshot_path)
        except OSError as exc:
            logger.error("Failed to persist snapshot at %s: %s", self.snapshot_path, exc)
            # Best-effort cleanup of the partial file.
            try:
                tmp_path.unlink()
            except OSError:
                pass
            raise
        logger.info("Snapshot saved at %s", snapshot["timestamp"])

    def load_snapshot(self) -> dict[str, Any] | None:
        """Load last saved snapshot for recovery."""
        if not self.snapshot_path.exists():
            return None
        try:
            data = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
            logger.info("Snapshot loaded from %s", data.get("timestamp"))
            return data.get("state")
        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.error("Failed to load snapshot: %s", e)
            return None

    def should_alert_stale(
        self, last_activity: datetime, timeout_minutes: int = 30
    ) -> bool:
        """Check if system is stale (no activity within timeout)."""
        now = datetime.now(timezone.utc)
        elapsed = (now - last_activity).total_seconds() / 60
        return elapsed > timeout_minutes
