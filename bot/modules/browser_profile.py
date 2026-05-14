"""Per-account Playwright user_data_dir paths (Phase I-C-1).

Each FB account gets its own persistent Chromium profile directory so
``localStorage``, ``IndexedDB``, service worker caches, and rotated
cookies survive across runs. Without this, every Playwright session
starts with a clean profile — FB's anti-bot reads "same cookie, brand
new device" and escalates the risk score → cookie flips EXPIRED fast.

Layout::

    $FB_PROFILE_ROOT/
        account-1/        # full Chromium user_data_dir for FBAccount id=1
        account-2/
        ...

Default ``$FB_PROFILE_ROOT`` is ``$HOME/.fb-bot/fb-profiles``. Override
via env var when the home dir is read-only or you want the data on a
different volume.

The helpers here only compute paths and (optionally) wipe them. They
don't ``mkdir`` lazily — the caller (``create_persistent_session``)
decides when to materialize the directory, so unit tests can assert
"path computed but not yet on disk".
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path


def get_profile_root() -> Path:
    """Return the root directory holding all per-account profiles.

    Resolution order:
        1. ``$FB_PROFILE_ROOT`` env var (absolute path)
        2. ``$HOME/.fb-bot/fb-profiles`` (POSIX) /
           ``$USERPROFILE/.fb-bot/fb-profiles`` (Windows, via ``Path.home``)
    """
    override = os.getenv("FB_PROFILE_ROOT")
    if override:
        return Path(override)
    return Path.home() / ".fb-bot" / "fb-profiles"


def get_profile_path(account_id: int) -> Path:
    """Return ``<root>/account-<id>`` without creating it on disk.

    Caller is responsible for ``path.mkdir(parents=True, exist_ok=True)``
    when starting a persistent context.
    """
    return get_profile_root() / f"account-{account_id}"


def wipe_profile(account_id: int) -> None:
    """Remove the on-disk profile directory for ``account_id``.

    Idempotent: calling on a missing path is a no-op. Used by:
      * Phase I-C-4 — DELETE ``/fb-accounts/{id}`` cleanup hook.
      * Phase I-C-5 — POST ``/fb-accounts/{id}/re-upload-cookie`` taint
        recovery (a profile that triggered a login wall can't be salvaged
        by swapping cookies; nuke and re-bootstrap).
    """
    path = get_profile_path(account_id)
    shutil.rmtree(path, ignore_errors=True)
