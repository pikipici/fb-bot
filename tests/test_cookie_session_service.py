"""Tests for cookie parsing + profile fetching service."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from server.services.cookie_session_service import (
    CookieValidationError,
    ProfileInfo,
    parse_cookie_string,
    serialize_cookies,
    validate_and_fetch_profile,
)


# --- parse_cookie_string -------------------------------------------------


class TestParseCookieString:
    def test_simple_two_cookies(self):
        raw = "c_user=123; xs=abc"
        out = parse_cookie_string(raw)
        assert out == {"c_user": "123", "xs": "abc"}

    def test_trailing_semicolon(self):
        raw = "c_user=123; xs=abc;"
        out = parse_cookie_string(raw)
        assert out == {"c_user": "123", "xs": "abc"}

    def test_extra_whitespace(self):
        raw = "  c_user = 123  ;   xs= abc  "
        out = parse_cookie_string(raw)
        assert out == {"c_user": "123", "xs": "abc"}

    def test_duplicate_keys_picks_last(self):
        raw = "c_user=111; c_user=222"
        out = parse_cookie_string(raw)
        assert out == {"c_user": "222"}

    def test_value_with_equals_preserved(self):
        raw = "xs=abc=def=ghi; c_user=123"
        out = parse_cookie_string(raw)
        assert out == {"xs": "abc=def=ghi", "c_user": "123"}

    def test_malformed_missing_equals_skipped(self):
        raw = "c_user=123; broken; xs=abc"
        out = parse_cookie_string(raw)
        assert out == {"c_user": "123", "xs": "abc"}

    def test_empty_string_returns_empty_dict(self):
        assert parse_cookie_string("") == {}

    def test_only_whitespace(self):
        assert parse_cookie_string("   ;  ;  ") == {}

    def test_empty_value_allowed(self):
        out = parse_cookie_string("c_user=; xs=abc")
        assert out == {"c_user": "", "xs": "abc"}


# --- serialize_cookies ----------------------------------------------------


class TestSerializeCookies:
    def test_roundtrip(self):
        original = {"c_user": "123", "xs": "abc", "datr": "xyz"}
        s = serialize_cookies(original)
        assert parse_cookie_string(s) == original

    def test_preserves_order(self):
        original = {"a": "1", "b": "2", "c": "3"}
        s = serialize_cookies(original)
        # Format should be "a=1; b=2; c=3"
        assert s == "a=1; b=2; c=3"


# --- validate_and_fetch_profile ------------------------------------------


@pytest.mark.asyncio
class TestValidateAndFetchProfile:
    async def test_happy_path_extracts_profile(self):
        # m.facebook.com/me redirects to user's profile page with their
        # name in <title> and user_id in a script tag.
        cookies = {"c_user": "100001234567890", "xs": "abc"}
        html = """
        <html><head><title>Budi Santoso | Facebook</title></head>
        <body>
        <script>{"USER_ID":"100001234567890","PROFILE_PIC":"https://scontent.fb.com/pic.jpg"}</script>
        </body></html>
        """

        mock_response = httpx.Response(
            200,
            text=html,
            request=httpx.Request("GET", "https://m.facebook.com/me"),
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            client_instance = AsyncMock()
            client_instance.__aenter__.return_value = client_instance
            client_instance.__aexit__.return_value = None
            client_instance.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = client_instance

            profile = await validate_and_fetch_profile(cookies)

        assert isinstance(profile, ProfileInfo)
        assert profile.fb_user_id == "100001234567890"
        assert profile.name == "Budi Santoso"
        assert "scontent.fb.com" in (profile.profile_pic_url or "")

    async def test_invalid_cookies_redirects_to_login(self):
        cookies = {"c_user": "bad", "xs": "bad"}
        # When cookies are invalid, m.facebook.com/me redirects to /login/
        # which returns an HTML page with a login form.
        login_html = "<html><body><form action='/login/' method='POST'><input name='email'/></form></body></html>"

        mock_response = httpx.Response(
            200,
            text=login_html,
            request=httpx.Request("GET", "https://m.facebook.com/login/"),
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            client_instance = AsyncMock()
            client_instance.__aenter__.return_value = client_instance
            client_instance.__aexit__.return_value = None
            client_instance.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = client_instance

            with pytest.raises(CookieValidationError):
                await validate_and_fetch_profile(cookies)

    async def test_missing_c_user_raises_immediately(self):
        cookies = {"datr": "xyz"}  # no c_user
        with pytest.raises(CookieValidationError):
            await validate_and_fetch_profile(cookies)

    async def test_empty_cookies_raises(self):
        with pytest.raises(CookieValidationError):
            await validate_and_fetch_profile({})

    async def test_network_error_raises_validation_error(self):
        cookies = {"c_user": "123", "xs": "abc"}

        with patch("httpx.AsyncClient") as mock_client_cls:
            client_instance = AsyncMock()
            client_instance.__aenter__.return_value = client_instance
            client_instance.__aexit__.return_value = None
            client_instance.get = AsyncMock(
                side_effect=httpx.ConnectError("connection failed")
            )
            mock_client_cls.return_value = client_instance

            with pytest.raises(CookieValidationError):
                await validate_and_fetch_profile(cookies)

    async def test_non_200_status_raises(self):
        cookies = {"c_user": "123", "xs": "abc"}
        mock_response = httpx.Response(
            500,
            text="server error",
            request=httpx.Request("GET", "https://m.facebook.com/me"),
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            client_instance = AsyncMock()
            client_instance.__aenter__.return_value = client_instance
            client_instance.__aexit__.return_value = None
            client_instance.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = client_instance

            with pytest.raises(CookieValidationError):
                await validate_and_fetch_profile(cookies)
