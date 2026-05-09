"""Tests for ``RecoveryManager``.

Focus:
* Round-trip save + load.
* Atomic write: a simulated crash mid-write must not leave a
  truncated snapshot at the final path.
* load_snapshot tolerates malformed or missing files.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from bot.modules.recovery import RecoveryManager


@pytest.fixture
def manager(tmp_path):
    return RecoveryManager(data_dir=str(tmp_path))


class TestRoundTrip:
    def test_save_then_load(self, manager):
        manager.save_snapshot({"a": 1, "b": [1, 2, 3]})
        loaded = manager.load_snapshot()
        assert loaded == {"a": 1, "b": [1, 2, 3]}

    def test_no_snapshot_returns_none(self, manager):
        assert manager.load_snapshot() is None


class TestAtomicity:
    def test_snapshot_file_is_complete_json(self, manager):
        manager.save_snapshot({"k": "v"})
        raw = manager.snapshot_path.read_text()
        # If this parses, the file is never half-written.
        data = json.loads(raw)
        assert data["state"] == {"k": "v"}

    def test_failed_write_does_not_corrupt_existing(self, manager):
        """Simulate a crash during write_text on the tmp file: previous
        snapshot must stay intact and no partial tmp file is left."""
        manager.save_snapshot({"original": True})
        original = manager.snapshot_path.read_text()

        def _boom(*_args, **_kwargs):
            raise OSError("disk full")

        with patch.object(Path, "write_text", _boom):
            with pytest.raises(OSError):
                manager.save_snapshot({"new": True})

        # Original snapshot file is untouched.
        assert manager.snapshot_path.read_text() == original
        # tmp file was cleaned up if it existed.
        tmp = manager.snapshot_path.with_suffix(manager.snapshot_path.suffix + ".tmp")
        assert not tmp.exists()


class TestLoadFailure:
    def test_malformed_json_returns_none(self, manager):
        manager.data_dir.mkdir(parents=True, exist_ok=True)
        manager.snapshot_path.write_text("{not: valid json")
        assert manager.load_snapshot() is None
