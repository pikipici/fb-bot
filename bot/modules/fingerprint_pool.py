"""Stable pool of browser fingerprints.

Phase I-A of Session Hardening — per-account UA + viewport pinning. Called
once per account (from ``FBAccountService.ensure_fingerprint``) and persisted
to the ``fb_accounts`` row so subsequent Playwright sessions for the same
account reuse the exact same fingerprint.

Rationale
---------
FB's anti-bot surface flags session hijacking patterns when the same
``c_user`` / ``xs`` cookie pair shows up paired with wildly varying
``User-Agent``, viewport size, or device hints. Prior to Phase I the project
called ``random.choice(_VIEWPORT_PRESETS)`` at session build time, which meant
every 15-minute scanner tick presented FB with a fresh-looking "device". By
pinning one UA + one viewport per account we make the session look stable
("single device that lives at this account's household").

Pool choices intentionally omit mobile/WebKit strings — those pull in a
separate mobile FB UI (m.facebook.com) that our scraper DOM selectors don't
target.
"""

from __future__ import annotations

import random
from typing import Final

# Current-gen Chrome stable (Chrome 146-147 in mid-2026). Rotate this list
# once a quarter or the UA starts looking dated vs. real browser fleets.
_UA_POOL: Final[tuple[str, ...]] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
)

# Common desktop viewports. Exclude mobile/portrait to stay on www.facebook.com
# desktop layout that our selectors target.
_VIEWPORT_POOL: Final[tuple[tuple[int, int], ...]] = (
    (1366, 768),
    (1440, 900),
    (1536, 864),
    (1600, 900),
    (1920, 1080),
)


def pick_ua() -> str:
    """Pick a UA string from the pool. Call ONCE per account then persist."""
    return random.choice(_UA_POOL)


def pick_viewport() -> tuple[int, int]:
    """Pick a (width, height) tuple from the pool. Persist the result."""
    return random.choice(_VIEWPORT_POOL)
