"""Cookie parsing + profile fetching for the cookie-connect flow.

Flow:
    user_paste_raw_string
        -> parse_cookie_string() : dict[str, str]
        -> validate_and_fetch_profile() : ProfileInfo
             step 1: c_user cookie → fb_user_id (authoritative)
             step 2: m.facebook.com/me → session validity check (200 + not /login)
             step 3: m.facebook.com/profile.php?id={id} → follow redirect to
                      ``/p/<Name-Slug>-<id>/`` or ``/<vanity>/`` → derive name
             step 4: graph.facebook.com/<id>/picture?redirect=0&type=large
                      → JSON ``{data:{url, is_silhouette}}`` → profile_pic_url
        -> (if valid) serialize back + Fernet encrypt + save to fb_accounts

Design notes:
- We target the mobile surfaces (``m.facebook.com``) because the desktop
  ``www.facebook.com`` returns HTTP 400 without the right mix of headers
  and doesn't expose a parseable profile shell anyway.
- Graph picture endpoint works without any auth token — it's a public
  CDN redirect gateway. We use ``redirect=0`` so we get JSON back
  instead of a 302 and can persist the URL directly.
- Name extraction is best-effort. FB no longer ships plaintext names in
  HTML, but profile.php always redirects to a canonical vanity URL
  that encodes the display name in the slug.
"""
from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from typing import Final

import httpx

_VALIDATE_URL: Final = "https://m.facebook.com/me"
_PROFILE_REDIRECT_URL_TMPL: Final = (
    "https://m.facebook.com/profile.php?id={user_id}"
)
_GRAPH_PICTURE_URL_TMPL: Final = (
    "https://graph.facebook.com/{user_id}/picture?redirect=0&type=large"
)
_DEFAULT_USER_AGENT: Final = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
    "Mobile/15E148 Safari/604.1"
)
_TIMEOUT: Final = httpx.Timeout(10.0, connect=5.0)

# Matches ``/p/Some-Name-Slug-12345/`` or ``/somevanityname/``.
_SLUG_WITH_ID_RE: Final = re.compile(r"^/p/(.+?)-(\d+)/?$")
_VANITY_RE: Final = re.compile(r"^/([A-Za-z0-9._-]+)/?$")


class CookieValidationError(Exception):
    """Raised when a cookie bundle is missing required fields, rejected by
    Facebook, or when the validator couldn't reach m.facebook.com.
    """


@dataclass(frozen=True)
class ProfileInfo:
    fb_user_id: str
    name: str
    profile_pic_url: str | None


def parse_cookie_string(raw: str) -> dict[str, str]:
    """Parse a ``Cookie:`` header-style string into a dict.

    Tolerant: trims whitespace, keeps last value on duplicate keys, drops
    fragments without an ``=`` sign. Preserves ``=`` characters inside
    the value (e.g. base64 padding).
    """
    out: dict[str, str] = {}
    if not raw:
        return out
    for piece in raw.split(";"):
        piece = piece.strip()
        if not piece or "=" not in piece:
            continue
        name, _, value = piece.partition("=")
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        out[name] = value
    return out


def serialize_cookies(cookies: dict[str, str]) -> str:
    """Reverse of :func:`parse_cookie_string`. Stable ordering by input
    order; output is suitable for a ``Cookie:`` header or for re-storing
    the bundle.
    """
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def _name_from_redirect_path(path: str) -> str | None:
    """Extract a human-readable name from a profile-redirect URL path.

    FB redirects ``/profile.php?id=<id>`` to one of:
      * ``/p/<Name-With-Dashes>-<id>/`` — auto-slug fallback when no vanity
      * ``/<vanity-name>/`` — when the account set a vanity URL
    """
    m = _SLUG_WITH_ID_RE.match(path)
    if m:
        slug = m.group(1)
        decoded = urllib.parse.unquote(slug).replace("-", " ").strip()
        return decoded or None

    m = _VANITY_RE.match(path)
    if m:
        vanity = m.group(1)
        # Skip known non-profile paths that could match the bare regex.
        if vanity.lower() in {
            "home.php",
            "me",
            "profile",
            "login",
            "checkpoint",
            "help",
        }:
            return None
        return urllib.parse.unquote(vanity).replace(".", " ").strip() or None

    return None


async def _fetch_profile_name(
    client: httpx.AsyncClient, user_id: str
) -> str | None:
    """Resolve display name via profile.php redirect."""
    try:
        response = await client.get(
            _PROFILE_REDIRECT_URL_TMPL.format(user_id=user_id)
        )
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    final_url = str(response.request.url)
    parsed = urllib.parse.urlparse(final_url)
    return _name_from_redirect_path(parsed.path)


async def _fetch_profile_picture(
    client: httpx.AsyncClient, user_id: str
) -> str | None:
    """Resolve profile picture URL via the public graph redirect gateway.

    The endpoint is unauthenticated and returns JSON in the form
    ``{"data": {"url": "...", "is_silhouette": bool, ...}}``. Silhouette
    placeholders are still valid URLs; we persist them so the UI can
    show a consistent avatar.
    """
    try:
        response = await client.get(
            _GRAPH_PICTURE_URL_TMPL.format(user_id=user_id)
        )
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    data = payload.get("data") or {}
    url = data.get("url")
    return url if isinstance(url, str) and url else None


async def validate_and_fetch_profile(cookies: dict[str, str]) -> ProfileInfo:
    """Hit ``m.facebook.com/me`` with ``cookies`` and return the profile.

    Raises :class:`CookieValidationError` when:
    - ``c_user`` cookie is missing
    - the response URL contains ``/login`` (session rejected)
    - the response status is not 200
    - the network call fails
    """
    if not cookies:
        raise CookieValidationError(
            "Cookie kosong. Paste ulang cookie dari extension lu."
        )
    user_id = cookies.get("c_user")
    if not user_id:
        raise CookieValidationError(
            "Cookie 'c_user' gak ketemu. Pastiin lu export semua cookie "
            "domain facebook.com."
        )

    headers = {
        "User-Agent": _DEFAULT_USER_AGENT,
        "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
    }

    try:
        async with httpx.AsyncClient(
            headers=headers,
            cookies=cookies,
            timeout=_TIMEOUT,
            follow_redirects=True,
        ) as client:
            validate_response = await client.get(_VALIDATE_URL)

            if validate_response.status_code != 200:
                raise CookieValidationError(
                    f"Facebook balas status {validate_response.status_code}"
                    " — cookie mungkin gak valid."
                )
            final_url = str(validate_response.request.url)
            if "/login" in final_url or "login.php" in final_url:
                raise CookieValidationError(
                    "Cookie udah expired atau invalid. Login ulang di "
                    "browser lu dan paste ulang cookie."
                )

            name = await _fetch_profile_name(client, user_id)
            profile_pic_url = await _fetch_profile_picture(client, user_id)

    except CookieValidationError:
        raise
    except httpx.HTTPError as exc:
        raise CookieValidationError(
            f"Gagal hubungi Facebook buat validasi cookie: {exc}"
        ) from exc

    return ProfileInfo(
        fb_user_id=user_id,
        name=name or f"User {user_id}",
        profile_pic_url=profile_pic_url,
    )
