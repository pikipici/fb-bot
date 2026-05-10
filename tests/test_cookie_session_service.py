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
        assert s == "a=1; b=2; c=3"


# --- validate_and_fetch_profile ------------------------------------------


def _make_response(
    status: int = 200,
    *,
    text: str = "",
    json_body: dict | None = None,
    final_url: str = "https://m.facebook.com/me",
) -> httpx.Response:
    request = httpx.Request("GET", final_url)
    if json_body is not None:
        import json

        return httpx.Response(status, text=json.dumps(json_body), request=request)
    return httpx.Response(status, text=text, request=request)


def _install_sequential_get(monkeypatch_target, responses):
    """Replace httpx.AsyncClient so ``.get(url)`` returns queued responses
    in order. Each queued item may be a Response or a callable(url) ->
    Response for URL-conditional behavior.
    """
    it = iter(responses)

    async def fake_get(url, *args, **kwargs):
        try:
            nxt = next(it)
        except StopIteration:  # pragma: no cover — defensive
            raise AssertionError(f"Unexpected extra GET to {url}") from None
        if callable(nxt):
            return nxt(url)
        return nxt

    client_instance = AsyncMock()
    client_instance.__aenter__.return_value = client_instance
    client_instance.__aexit__.return_value = None
    client_instance.get = fake_get
    monkeypatch_target.return_value = client_instance


@pytest.mark.asyncio
class TestValidateAndFetchProfile:
    async def test_happy_path_extracts_full_profile(self):
        cookies = {"c_user": "100001234567890", "xs": "abc"}
        validate_resp = _make_response(
            200, text="<html>ok</html>", final_url="https://m.facebook.com/me"
        )
        profile_resp = _make_response(
            200,
            text="<html/>",
            final_url="https://m.facebook.com/p/Digi-Markt-100001234567890/",
        )
        picture_resp = _make_response(
            200,
            json_body={
                "data": {
                    "height": 200,
                    "width": 200,
                    "is_silhouette": False,
                    "url": "https://scontent.xx.fbcdn.net/pic.jpg",
                }
            },
            final_url=(
                "https://graph.facebook.com/100001234567890/picture"
                "?redirect=0&type=large"
            ),
        )
        with patch("httpx.AsyncClient") as mock_client_cls:
            _install_sequential_get(
                mock_client_cls, [validate_resp, profile_resp, picture_resp]
            )
            profile = await validate_and_fetch_profile(cookies)

        assert isinstance(profile, ProfileInfo)
        assert profile.fb_user_id == "100001234567890"
        assert profile.name == "Digi Markt"
        assert profile.profile_pic_url == "https://scontent.xx.fbcdn.net/pic.jpg"

    async def test_vanity_redirect_parses_name(self):
        cookies = {"c_user": "100001", "xs": "abc"}
        validate_resp = _make_response(200, text="ok")
        profile_resp = _make_response(
            200, final_url="https://m.facebook.com/zuck/"
        )
        picture_resp = _make_response(
            200, json_body={"data": {"url": "https://fbcdn.net/x.jpg"}}
        )
        with patch("httpx.AsyncClient") as mock_client_cls:
            _install_sequential_get(
                mock_client_cls, [validate_resp, profile_resp, picture_resp]
            )
            profile = await validate_and_fetch_profile(cookies)
        assert profile.name == "zuck"

    async def test_name_unresolved_falls_back_to_user_id(self):
        cookies = {"c_user": "100001", "xs": "abc"}
        validate_resp = _make_response(200, text="ok")
        # Profile request fails — caller falls back.
        profile_resp = _make_response(
            500, text="oops", final_url="https://m.facebook.com/err"
        )
        picture_resp = _make_response(
            200, json_body={"data": {"url": "https://fbcdn.net/x.jpg"}}
        )
        with patch("httpx.AsyncClient") as mock_client_cls:
            _install_sequential_get(
                mock_client_cls, [validate_resp, profile_resp, picture_resp]
            )
            profile = await validate_and_fetch_profile(cookies)
        assert profile.name == "User 100001"
        assert profile.profile_pic_url == "https://fbcdn.net/x.jpg"

    async def test_silhouette_picture_returns_none(self):
        """If Graph reports ``is_silhouette: true`` we treat the URL as no
        real avatar and return ``None`` so the UI falls back to a local
        placeholder instead of rendering FB's generic grey silhouette.
        """
        cookies = {"c_user": "100001", "xs": "abc"}
        validate_resp = _make_response(200, text="ok")
        profile_resp = _make_response(
            200, final_url="https://m.facebook.com/p/Foo-Bar-100001/"
        )
        picture_resp = _make_response(
            200,
            json_body={
                "data": {
                    "height": 200,
                    "is_silhouette": True,
                    "url": "https://scontent.xx.fbcdn.net/default.jpg",
                }
            },
        )
        with patch("httpx.AsyncClient") as mock_client_cls:
            _install_sequential_get(
                mock_client_cls, [validate_resp, profile_resp, picture_resp]
            )
            profile = await validate_and_fetch_profile(cookies)
        assert profile.name == "Foo Bar"
        assert profile.profile_pic_url is None

    async def test_picture_failure_returns_none_pic_but_still_valid(self):
        cookies = {"c_user": "100001", "xs": "abc"}
        validate_resp = _make_response(200, text="ok")
        profile_resp = _make_response(
            200, final_url="https://m.facebook.com/p/Foo-Bar-100001/"
        )
        picture_resp = _make_response(500, text="boom")
        with patch("httpx.AsyncClient") as mock_client_cls:
            _install_sequential_get(
                mock_client_cls, [validate_resp, profile_resp, picture_resp]
            )
            profile = await validate_and_fetch_profile(cookies)
        assert profile.name == "Foo Bar"
        assert profile.profile_pic_url is None

    async def test_invalid_cookies_redirects_to_login(self):
        cookies = {"c_user": "bad", "xs": "bad"}
        validate_resp = _make_response(
            200, text="<form/>", final_url="https://m.facebook.com/login/"
        )
        with patch("httpx.AsyncClient") as mock_client_cls:
            _install_sequential_get(mock_client_cls, [validate_resp])
            with pytest.raises(CookieValidationError):
                await validate_and_fetch_profile(cookies)

    async def test_missing_c_user_raises_immediately(self):
        cookies = {"datr": "xyz"}
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

    async def test_non_200_validate_status_raises(self):
        cookies = {"c_user": "123", "xs": "abc"}
        validate_resp = _make_response(500, text="server error")
        with patch("httpx.AsyncClient") as mock_client_cls:
            _install_sequential_get(mock_client_cls, [validate_resp])
            with pytest.raises(CookieValidationError):
                await validate_and_fetch_profile(cookies)
