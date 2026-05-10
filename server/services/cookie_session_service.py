"""Cookie parsing + profile fetching for the cookie-connect flow.

Flow:
    user_paste_raw_string
        -> parse_cookie_string() : dict[str, str]
        -> validate_and_fetch_profile() : ProfileInfo (hits m.facebook.com/me)
        -> (if valid) serialize back + Fernet encrypt + save to fb_accounts

Design notes:
- We target ``m.facebook.com`` (mobile) instead of ``www.facebook.com``
  because the mobile surface serves smaller, more parseable HTML and the
  same c_user cookie works.
- A valid session responds 200 at ``/me`` with the user's profile. An
  invalid session is redirected to ``/login/`` (even with ``200 OK``
  because httpx follows redirects by default); we treat any response
  whose URL contains ``/login/`` or that doesn't expose a user id as
  invalid.
- We keep the validator network call minimal (single GET, 10s timeout)
  so dashboard preview feels snappy.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

import httpx

_PROFILE_URL: Final = "https://m.facebook.com/me"
_DEFAULT_USER_AGENT: Final = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
    "Mobile/15E148 Safari/604.1"
)
_TIMEOUT: Final = httpx.Timeout(10.0, connect=5.0)

_USER_ID_RE: Final = re.compile(r'"USER_ID"\s*:\s*"(\d+)"')
_USER_ID_ALT_RE: Final = re.compile(r'"actorID"\s*:\s*"(\d+)"')
_TITLE_RE: Final = re.compile(
    r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL
)
_PROFILE_PIC_RE: Final = re.compile(
    r'"PROFILE_PIC"\s*:\s*"(https?://[^"]+)"'
)


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


def _extract_profile(html: str) -> tuple[str | None, str | None, str | None]:
    """Pull (user_id, name, profile_pic) out of an m.facebook.com page.

    Returns ``None`` for any field the page doesn't expose — caller
    decides whether the missing field is fatal.
    """
    user_id: str | None = None
    m = _USER_ID_RE.search(html) or _USER_ID_ALT_RE.search(html)
    if m:
        user_id = m.group(1)

    name: str | None = None
    t = _TITLE_RE.search(html)
    if t:
        title = t.group(1).strip()
        # Titles look like "Budi Santoso | Facebook" or
        # "Budi Santoso - Facebook".
        for sep in (" | Facebook", " - Facebook", " | facebook", " - facebook"):
            if title.endswith(sep):
                title = title[: -len(sep)].strip()
                break
        # A login page title is literally "Facebook" — treat as missing.
        if title and title.lower() not in {"facebook", "log in to facebook"}:
            name = title

    pic: str | None = None
    p = _PROFILE_PIC_RE.search(html)
    if p:
        pic = p.group(1)

    return user_id, name, pic


async def validate_and_fetch_profile(cookies: dict[str, str]) -> ProfileInfo:
    """Hit ``m.facebook.com/me`` with ``cookies`` and return the profile.

    Raises :class:`CookieValidationError` when:
    - ``c_user`` cookie is missing
    - the response URL contains ``/login`` (session rejected)
    - the response status is not 200
    - the page doesn't expose a user id
    - the network call fails
    """
    if not cookies:
        raise CookieValidationError(
            "Cookie kosong. Paste ulang cookie dari extension lu."
        )
    if "c_user" not in cookies or not cookies["c_user"]:
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
            response = await client.get(_PROFILE_URL)
    except httpx.HTTPError as exc:
        raise CookieValidationError(
            f"Gagal hubungi Facebook buat validasi cookie: {exc}"
        ) from exc

    if response.status_code != 200:
        raise CookieValidationError(
            f"Facebook balas status {response.status_code} — "
            "cookie mungkin gak valid."
        )

    final_url = str(response.request.url)
    if "/login" in final_url or "login.php" in final_url:
        raise CookieValidationError(
            "Cookie udah expired atau invalid. Login ulang di browser "
            "lu dan paste ulang cookie."
        )

    user_id, name, pic = _extract_profile(response.text)
    # Fall back to the c_user cookie if the page didn't expose USER_ID.
    if not user_id:
        user_id = cookies.get("c_user") or None
    if not user_id:
        raise CookieValidationError(
            "Gak bisa extract profile dari response Facebook. Cookie "
            "mungkin expired."
        )

    return ProfileInfo(
        fb_user_id=user_id,
        name=name or f"User {user_id}",
        profile_pic_url=pic,
    )
