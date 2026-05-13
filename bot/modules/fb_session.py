"""Playwright cookie-session helper for Facebook scraping.

Takes a decrypted cookie dict (from ``cookie_session_service``) and
produces a configured :class:`BrowserContext` that impersonates a
logged-in FB user:

- Injects cookies into ``.facebook.com`` so every subsequent request
  carries the session.
- Sets a realistic desktop viewport so server-side fingerprinting
  doesn't immediately flag us as a bot.
- Forces ``id-ID`` locale + ``Asia/Jakarta`` timezone so the session
  looks consistent with the user's actual account country.

Usage::

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await create_session_context(browser, cookies_dict)
        page = await context.new_page()
        await page.goto("https://m.facebook.com/me")

Rationale:
- Cookies carry ``.facebook.com`` domain (leading dot) so subdomains
  ``m.facebook.com``, ``www.facebook.com`` and ``mbasic.facebook.com``
  all accept them without re-login redirects.
- ``SameSite=Lax`` matches the default FB issues its own cookies under,
  which keeps top-level navigation cookies attached.
- ``httpOnly=False`` because we don't need JS to read them but it also
  doesn't matter for scraping; FB itself sets most as non-httpOnly.
"""
from __future__ import annotations

import random
from typing import Any, Final

DEFAULT_USER_AGENT: Final = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# Phase I-E-1 — stealth init script injected into every BrowserContext via
# ``context.add_init_script`` before the first navigation. Purpose: hide the
# three cheapest/highest-signal tells that separate headless Chromium from
# real Chrome, which Facebook's anti-bot reads on every page load:
#
#   * ``navigator.webdriver`` — ``true`` under Playwright by default; real
#     Chrome always reports ``false``.
#   * ``navigator.plugins.length`` — ``0`` under headless; real Chrome has
#     at least the built-in PDF viewer plugins.
#   * ``navigator.languages`` — we set ``locale=id-ID`` on the context, so
#     ``languages`` must lead with the Indonesian tag to match.
#   * ``window.chrome`` — undefined under headless; real Chrome always has
#     it populated. A minimal shim is enough for feature-detects.
#
# Keep the patch intentionally minimal — full playwright-stealth is parked
# in the roadmap (§6) pending evidence I-E alone is insufficient.
STEALTH_INIT_SCRIPT: Final = """
Object.defineProperty(navigator, 'webdriver', { get: () => false });
Object.defineProperty(navigator, 'plugins', {
  get: () => [
    { name: 'Chrome PDF Plugin' },
    { name: 'Chrome PDF Viewer' },
    { name: 'Native Client' },
  ],
});
Object.defineProperty(navigator, 'languages', {
  get: () => ['id-ID', 'id', 'en-US', 'en'],
});
window.chrome = window.chrome || { runtime: {} };
""".strip()

# A handful of realistic desktop viewports. Pick one per session to add
# a bit of fingerprint variance without venturing into mobile layouts.
_VIEWPORT_PRESETS: Final = (
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1600, "height": 900},
    {"width": 1920, "height": 1080},
)


def cookies_dict_to_playwright_format(
    cookies: dict[str, str],
    *,
    domain: str = ".facebook.com",
) -> list[dict[str, Any]]:
    """Turn a flat ``{name: value}`` cookie dict into Playwright's
    ``context.add_cookies`` payload shape.

    Every entry is scoped to ``domain`` (default ``.facebook.com``),
    HTTPS-only, and with ``SameSite=Lax`` so top-level navigations carry
    them through redirects.
    """
    return [
        {
            "name": name,
            "value": value,
            "domain": domain,
            "path": "/",
            "secure": True,
            "httpOnly": False,
            "sameSite": "Lax",
        }
        for name, value in cookies.items()
    ]


async def create_session_context(
    browser: Any,
    cookies: dict[str, str],
    *,
    user_agent: str | None = None,
    viewport: dict[str, int] | None = None,
    locale: str = "id-ID",
    timezone_id: str = "Asia/Jakarta",
) -> Any:
    """Create a new Playwright ``BrowserContext`` pre-loaded with ``cookies``.

    Args:
        browser: a launched Playwright browser (chromium/firefox/webkit).
        cookies: decrypted cookie dict. Pass ``{}`` to create a blank
            context — caller decides whether that's useful.
        user_agent: UA string to pin. Defaults to
            :data:`DEFAULT_USER_AGENT`.
        viewport: override viewport dict. Defaults to a random desktop
            preset from :data:`_VIEWPORT_PRESETS`.
        locale: ``Accept-Language`` and ``navigator.language``. Defaults
            to Indonesia so FB renders the ID UI we parse against.
        timezone_id: IANA tz string. Defaults to ``Asia/Jakarta``.

    Returns:
        the new :class:`BrowserContext` with cookies already injected.
    """
    ua = user_agent or DEFAULT_USER_AGENT
    vp = viewport or random.choice(_VIEWPORT_PRESETS)

    context = await browser.new_context(
        user_agent=ua,
        viewport=vp,
        locale=locale,
        timezone_id=timezone_id,
    )
    # Phase I-E-1 — register stealth patch BEFORE injecting cookies and
    # BEFORE the first navigation. ``add_init_script`` attaches to every
    # future page in this context, so every request FB sees (incl. the
    # initial document load) evaluates against the patched navigator.
    await context.add_init_script(STEALTH_INIT_SCRIPT)
    await context.add_cookies(cookies_dict_to_playwright_format(cookies))
    return context


async def capture_cookies_from_context(
    context: Any, *, domain_suffix: str = "facebook.com"
) -> dict[str, str]:
    """Harvest the current cookie state from a live BrowserContext.

    Phase I-B-1 — FB rotates session cookies (``xs`` in particular) mid-
    session via ``Set-Cookie`` headers. If we only ever write the cookie
    dict we started with, the DB row drifts away from what the browser
    actually used, and the next scanner tick ends up presenting the
    pre-rotation value — which FB treats as a stale session.

    Call this after a successful interaction (scan/send) and before the
    context closes. Returns a flat ``{name: value}`` dict filtered to
    cookies whose ``domain`` ends in ``domain_suffix`` — this catches both
    leading-dot (``.facebook.com``) and bare-subdomain forms
    (``m.facebook.com`` / ``www.facebook.com``).

    Tolerates ``cookies()`` returning ``None`` or empty lists — returns
    ``{}`` rather than raising, because this helper runs inside a
    ``finally`` block and must never mask the real exception.
    """
    raw = await context.cookies()
    if not raw:
        return {}
    out: dict[str, str] = {}
    for c in raw:
        domain = (c.get("domain") or "").lower()
        if not domain.endswith(domain_suffix):
            continue
        name = c.get("name")
        if not name:
            continue
        out[name] = c.get("value", "")
    return out
