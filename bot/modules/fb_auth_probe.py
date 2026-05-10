"""FB Auth Probe — detect cookie expiry via DOM markers.

Why this exists
---------------
FB's URL-based redirect check (``/login``, ``/checkpoint``) misses a
common failure mode: when cookies are invalidated, FB often keeps the
original URL but renders a **login wall / account chooser** in the
body. The page looks authenticated by URL but the DOM is actually:

    "Masuk Facebook" / "Log in to Facebook"
    "Gunakan profil lain" / "Use another profile"
    "Buat akun baru" / "Create new account"

Callers (``comment_sender``, ``source_collector``) use :func:`is_login_wall`
as a second-line check after the URL-fragment check. When true, the
caller raises ``CookieExpiredError`` and the orchestrator flips the
account to ``EXPIRED`` via :meth:`FBAccountService.mark_cookies_expired`.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# JS probe — runs in-page, returns `{loginMarker: bool, reason: str}`.
# Matches login/account-chooser text in ANY top-level language we've
# seen in the wild (ID/EN). Kept DOM-anchored so CSS-class roulette
# doesn't break it.
_LOGIN_WALL_PROBE_JS = r"""
() => {
    // 1. Any explicit login anchor? (cheap, strong signal)
    if (document.querySelector('a[href*="login.php"], a[href*="/login/"]')) {
        return { loginMarker: true, reason: 'login_anchor' };
    }

    // 2. Account-chooser / login-wall phrases in the top ~2KB of body
    //    text. Truncating guards against legit posts that happen to
    //    quote the word "Masuk".
    const body = (document.body && document.body.innerText) || '';
    const head = body.slice(0, 2000);
    const markers = [
        'Masuk Facebook',
        'Log in to Facebook',
        'Gunakan profil lain',
        'Use another profile',
        'Buat akun baru',
        'Create new account',
        'Lupa Akun?',
        'Forgot account?',
    ];
    for (const m of markers) {
        if (head.includes(m)) {
            return { loginMarker: true, reason: 'text:' + m };
        }
    }

    // 3. The login form itself — email+password inputs visible at root.
    const email = document.querySelector(
        'input[name="email"], input[id="email"]'
    );
    const pass = document.querySelector(
        'input[name="pass"], input[type="password"]'
    );
    if (email && pass && email.offsetParent && pass.offsetParent) {
        return { loginMarker: true, reason: 'login_form' };
    }

    return { loginMarker: false, reason: '' };
};
"""


async def is_login_wall(page: Any) -> bool:
    """Return True iff the page DOM shows a login wall / account chooser.

    Never raises — any probe failure (page crashed, eval timeout, mock
    that doesn't implement ``evaluate``) returns False so callers keep
    their existing happy-path behavior.
    """
    try:
        result = await page.evaluate(_LOGIN_WALL_PROBE_JS)
    except Exception:  # pragma: no cover — defensive against mocks
        logger.debug("is_login_wall eval failed", exc_info=True)
        return False

    if not isinstance(result, dict):
        return False
    marker = bool(result.get("loginMarker"))
    if marker:
        logger.info(
            "login wall detected — reason=%s", result.get("reason", "")
        )
    return marker


async def login_wall_reason(page: Any) -> str | None:
    """Return the matched marker reason, or None if no login wall."""
    try:
        result = await page.evaluate(_LOGIN_WALL_PROBE_JS)
    except Exception:  # pragma: no cover
        return None
    if not isinstance(result, dict):
        return None
    if not result.get("loginMarker"):
        return None
    reason = result.get("reason") or ""
    return str(reason) if reason else "unknown"
