"""Tests for bot.modules.fb_auth_probe — DOM-based login-wall detection."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.modules.fb_auth_probe import is_login_wall, login_wall_reason


def _page_with_eval(return_value):
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=return_value)
    return page


class TestIsLoginWall:
    @pytest.mark.asyncio
    async def test_positive_marker_returns_true(self):
        page = _page_with_eval({"loginMarker": True, "reason": "login_anchor"})
        assert await is_login_wall(page) is True

    @pytest.mark.asyncio
    async def test_negative_marker_returns_false(self):
        page = _page_with_eval({"loginMarker": False, "reason": ""})
        assert await is_login_wall(page) is False

    @pytest.mark.asyncio
    async def test_eval_exception_returns_false_never_raises(self):
        page = MagicMock()
        page.evaluate = AsyncMock(side_effect=RuntimeError("page crashed"))
        # Must not raise — a failing probe should not abort the caller.
        assert await is_login_wall(page) is False

    @pytest.mark.asyncio
    async def test_non_dict_result_returns_false(self):
        page = _page_with_eval("not a dict")
        assert await is_login_wall(page) is False

    @pytest.mark.asyncio
    async def test_none_result_returns_false(self):
        page = _page_with_eval(None)
        assert await is_login_wall(page) is False


class TestLoginWallReason:
    @pytest.mark.asyncio
    async def test_returns_reason_when_detected(self):
        page = _page_with_eval(
            {"loginMarker": True, "reason": "text:Masuk Facebook"}
        )
        assert await login_wall_reason(page) == "text:Masuk Facebook"

    @pytest.mark.asyncio
    async def test_returns_none_when_not_detected(self):
        page = _page_with_eval({"loginMarker": False, "reason": ""})
        assert await login_wall_reason(page) is None

    @pytest.mark.asyncio
    async def test_returns_unknown_when_marker_true_but_empty_reason(self):
        page = _page_with_eval({"loginMarker": True, "reason": ""})
        assert await login_wall_reason(page) == "unknown"

    @pytest.mark.asyncio
    async def test_returns_none_on_eval_exception(self):
        page = MagicMock()
        page.evaluate = AsyncMock(side_effect=RuntimeError("boom"))
        assert await login_wall_reason(page) is None
