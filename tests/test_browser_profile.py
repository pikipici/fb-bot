"""Tests for ``bot/modules/browser_profile.py`` (Phase I-C-1).

Per-account Playwright user_data_dir helper: deterministic path under
``$FB_PROFILE_ROOT`` (default ``$HOME/.fb-bot/fb-profiles``).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_get_profile_root_uses_env_override(monkeypatch, tmp_path):
    """``FB_PROFILE_ROOT`` env var fully overrides the default."""
    from bot.modules.browser_profile import get_profile_root

    monkeypatch.setenv("FB_PROFILE_ROOT", str(tmp_path))
    assert get_profile_root() == tmp_path


def test_get_profile_root_default_under_home(monkeypatch, tmp_path):
    """Without env override, root is ``$HOME/.fb-bot/fb-profiles``."""
    from bot.modules.browser_profile import get_profile_root

    monkeypatch.delenv("FB_PROFILE_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    # ``Path.home()`` reads HOME on POSIX. Skip check on Windows CI where
    # USERPROFILE rules — use direct override there.
    if os.name == "nt":
        monkeypatch.setenv("USERPROFILE", str(tmp_path))

    root = get_profile_root()
    assert root == tmp_path / ".fb-bot" / "fb-profiles"


def test_get_profile_path_per_account(monkeypatch, tmp_path):
    """``get_profile_path(id)`` returns ``<root>/account-<id>``."""
    from bot.modules.browser_profile import get_profile_path

    monkeypatch.setenv("FB_PROFILE_ROOT", str(tmp_path))

    path = get_profile_path(42)
    assert path == tmp_path / "account-42"


def test_get_profile_path_creates_root_lazily(monkeypatch, tmp_path):
    """Path is computed lazily — caller decides when to ``mkdir``."""
    from bot.modules.browser_profile import get_profile_path

    nested = tmp_path / "nested" / "root"
    monkeypatch.setenv("FB_PROFILE_ROOT", str(nested))

    path = get_profile_path(1)
    # We don't auto-mkdir at lookup time.
    assert not nested.exists()
    assert path.parent == nested


def test_wipe_profile_removes_dir_idempotent(monkeypatch, tmp_path):
    """``wipe_profile(id)`` removes the dir; safe to call when missing."""
    from bot.modules.browser_profile import get_profile_path, wipe_profile

    monkeypatch.setenv("FB_PROFILE_ROOT", str(tmp_path))

    pdir = get_profile_path(7)
    pdir.mkdir(parents=True)
    (pdir / "sentinel").write_text("x")
    assert pdir.exists()

    wipe_profile(7)
    assert not pdir.exists()

    # Idempotent — second call is a no-op, must not raise.
    wipe_profile(7)
    assert not pdir.exists()


def test_wipe_profile_only_removes_target_dir(monkeypatch, tmp_path):
    """Sibling profiles must survive a ``wipe_profile`` call."""
    from bot.modules.browser_profile import get_profile_path, wipe_profile

    monkeypatch.setenv("FB_PROFILE_ROOT", str(tmp_path))

    p1 = get_profile_path(1)
    p2 = get_profile_path(2)
    p1.mkdir(parents=True)
    p2.mkdir(parents=True)
    (p1 / "a").write_text("a")
    (p2 / "b").write_text("b")

    wipe_profile(1)
    assert not p1.exists()
    assert p2.exists()
    assert (p2 / "b").read_text() == "b"
