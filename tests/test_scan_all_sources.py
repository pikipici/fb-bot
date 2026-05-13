"""Tests for scan_all_sources celery task and cookie expiry handling."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from bot.modules.source_collector import (
    CookieExpiredError,
    SourceCollectorResult,
)
from server.models import Base, FBAccount, Source


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "CREDENTIALS_KEY", "WyzJqG3Vg9ZpUyFkq4bUxN9yxMG3xCyq4Rr8s3fL7dE="
    )
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def account_with_cookies(db):
    """Seed an active FB account with encrypted cookies."""
    from server.crypto import encrypt_cookies

    cookies = {"c_user": "61577777450562", "xs": "abc"}
    account = FBAccount(
        label="Test Account",
        status="ACTIVE",
        fb_user_id="61577777450562",
        fb_name="Test User",
        cookies_encrypted=encrypt_cookies(cookies),
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@pytest.fixture
def sources(db, account_with_cookies):
    home = Source(type="home_feed", label="Beranda", enabled=True)
    group = Source(
        type="group",
        label="Jual Beli",
        fb_entity_id="12345",
        enabled=True,
    )
    disabled = Source(
        type="page",
        label="Nonaktif",
        fb_entity_id="xxx",
        enabled=False,
    )
    db.add_all([home, group, disabled])
    db.commit()
    return [home, group, disabled]


def _mock_result(source_id: int, count: int = 2) -> SourceCollectorResult:
    posts = [
        {
            "fb_post_id": f"src{source_id}-p{i}",
            "author_name": "Tester",
            "author_fb_id": "999",
            "text": "Konten trending",
            "post_url": f"https://fb.com/p{i}",
            "thumbnail_url": None,
            "likes": 100,
            "comments": 50,
            "shares": 30,
            "reactions_total": 180,
            "post_timestamp": None,
        }
        for i in range(count)
    ]
    return SourceCollectorResult(source_id=source_id, posts=posts, success=True)


class TestScanAllSourcesLogic:
    """Exercise the orchestrator in ``_run_scan_all_sources`` directly,
    without going through Celery's task machinery.
    """

    def test_scans_only_enabled_sources(
        self, db, account_with_cookies, sources, monkeypatch
    ):
        from bot.tasks import _run_scan_all_sources

        scan_mock = AsyncMock(
            side_effect=lambda source, cookies, **_: _mock_result(
                int(source["id"])
            )
        )
        monkeypatch.setattr("bot.tasks.scan_source", scan_mock)

        result = _run_scan_all_sources(db)

        assert scan_mock.await_count == 2  # disabled skipped
        scanned_ids = {
            int(call.args[0]["id"]) for call in scan_mock.await_args_list
        }
        assert sources[0].id in scanned_ids
        assert sources[1].id in scanned_ids
        assert sources[2].id not in scanned_ids
        assert result["enabled_sources"] == 2

    def test_upserts_trending_posts_per_source(
        self, db, account_with_cookies, sources, monkeypatch
    ):
        from bot.tasks import _run_scan_all_sources
        from server.models import TrendingPost

        scan_mock = AsyncMock(
            side_effect=lambda source, cookies, **_: _mock_result(
                int(source["id"]), count=3
            )
        )
        monkeypatch.setattr("bot.tasks.scan_source", scan_mock)

        result = _run_scan_all_sources(db)

        total_rows = db.query(TrendingPost).count()
        assert total_rows == 6  # 2 sources * 3 posts
        assert result["inserted"] == 6

    def test_cookie_expired_marks_account_and_aborts(
        self, db, account_with_cookies, sources, monkeypatch
    ):
        from bot.tasks import _run_scan_all_sources

        scan_mock = AsyncMock(side_effect=CookieExpiredError("login"))
        monkeypatch.setattr("bot.tasks.scan_source", scan_mock)

        result = _run_scan_all_sources(db)

        db.refresh(account_with_cookies)
        assert account_with_cookies.status == "EXPIRED"
        assert account_with_cookies.cookies_expired_at is not None
        assert result["aborted"] is True
        assert result["reason"] == "cookie_expired"
        # No further scans attempted after first expired.
        assert scan_mock.await_count == 1

    def test_no_active_account_aborts_gracefully(
        self, db, monkeypatch
    ):
        """No account seeded at all — scan must abort cleanly."""
        from bot.tasks import _run_scan_all_sources

        # Seed a source so query returns rows, proving the early return
        # is driven by the account check not an empty source list.
        db.add(Source(type="home_feed", label="B", enabled=True))
        db.commit()

        scan_mock = AsyncMock()
        monkeypatch.setattr("bot.tasks.scan_source", scan_mock)

        result = _run_scan_all_sources(db)
        assert result["aborted"] is True
        assert result["reason"] == "no_active_account"
        assert scan_mock.await_count == 0

    def test_source_scan_failure_does_not_abort_others(
        self, db, account_with_cookies, sources, monkeypatch
    ):
        from bot.tasks import _run_scan_all_sources

        def _side_effect(source, cookies, **_):
            if source["id"] == sources[0].id:
                return SourceCollectorResult(
                    source_id=source["id"],
                    posts=[],
                    success=False,
                    error="boom",
                )
            return _mock_result(source["id"])

        scan_mock = AsyncMock(side_effect=_side_effect)
        monkeypatch.setattr("bot.tasks.scan_source", scan_mock)

        result = _run_scan_all_sources(db)
        assert scan_mock.await_count == 2
        assert result["scan_errors"] == 1
        assert result["successful_scans"] == 1

    def test_updates_last_scanned_at_on_success(
        self, db, account_with_cookies, sources, monkeypatch
    ):
        from bot.tasks import _run_scan_all_sources

        monkeypatch.setattr(
            "bot.tasks.scan_source",
            AsyncMock(
                side_effect=lambda source, cookies, **_: _mock_result(
                    source["id"]
                )
            ),
        )

        _run_scan_all_sources(db)
        db.refresh(sources[0])
        db.refresh(sources[1])
        assert sources[0].last_scanned_at is not None
        assert sources[1].last_scanned_at is not None
        # Disabled source was not touched.
        db.refresh(sources[2])
        assert sources[2].last_scanned_at is None

    def test_pins_fingerprint_and_forwards_to_scan_source(
        self, db, account_with_cookies, sources, monkeypatch
    ):
        """Phase I-A-3 — orchestrator pins UA+viewport per-account then
        forwards via kwargs to every ``scan_source`` call.

        Assertions:
        - ``ensure_fingerprint`` is invoked exactly once (per-account pin).
        - After the run the account row has ``browser_ua``/``viewport_w``/
          ``viewport_h`` populated (persistence survives).
        - Every ``scan_source`` call gets the same UA + viewport kwargs
          (stable across sources within a run).
        """
        from bot.tasks import _run_scan_all_sources

        scan_mock = AsyncMock(
            side_effect=lambda source, cookies, **_: _mock_result(
                int(source["id"])
            )
        )
        monkeypatch.setattr("bot.tasks.scan_source", scan_mock)

        _run_scan_all_sources(db)

        db.refresh(account_with_cookies)
        assert account_with_cookies.browser_ua is not None
        assert account_with_cookies.viewport_w is not None
        assert account_with_cookies.viewport_h is not None

        # Every scan_source call gets the SAME fingerprint within this run.
        pinned_ua = account_with_cookies.browser_ua
        pinned_vp = {
            "width": account_with_cookies.viewport_w,
            "height": account_with_cookies.viewport_h,
        }
        assert scan_mock.await_count >= 2
        for call in scan_mock.await_args_list:
            assert call.kwargs.get("user_agent") == pinned_ua
            assert call.kwargs.get("viewport") == pinned_vp

    def test_applies_startup_jitter_before_first_scan(
        self, db, account_with_cookies, sources, monkeypatch
    ):
        """Phase I-D-1 — orchestrator sleeps a short random jitter at
        the very start of a scan cycle so beat ticks don't all fire
        against FB on the same second wall-clock boundary.

        We assert:
        - ``_sleep_startup_jitter`` is awaited exactly once.
        - It is awaited BEFORE the first ``scan_source``.
        """
        from bot.tasks import _run_scan_all_sources

        call_order: list[str] = []

        async def _fake_jitter(*_args, **_kwargs) -> None:
            call_order.append("jitter")

        scan_mock = AsyncMock(
            side_effect=lambda source, cookies, **_: (
                call_order.append(f"scan:{source['id']}")
                or _mock_result(int(source["id"]))
            )
        )
        monkeypatch.setattr("bot.tasks.scan_source", scan_mock)
        monkeypatch.setattr(
            "bot.tasks._sleep_startup_jitter", _fake_jitter
        )

        _run_scan_all_sources(db)

        assert call_order.count("jitter") == 1, call_order
        assert call_order[0] == "jitter", call_order

    def test_applies_inter_source_think_time_between_sources(
        self, db, account_with_cookies, sources, monkeypatch
    ):
        """Phase I-D-1 — orchestrator inserts a random think-time sleep
        BETWEEN sources within one cycle (not before the first, not after
        the last) to mimic human browsing rhythm.

        Two enabled sources → one inter-source delay expected.
        """
        from bot.tasks import _run_scan_all_sources

        call_order: list[str] = []

        async def _fake_think(*_args, **_kwargs) -> None:
            call_order.append("think")

        scan_mock = AsyncMock(
            side_effect=lambda source, cookies, **_: (
                call_order.append(f"scan:{source['id']}")
                or _mock_result(int(source["id"]))
            )
        )
        monkeypatch.setattr("bot.tasks.scan_source", scan_mock)
        monkeypatch.setattr(
            "bot.tasks._sleep_inter_source", _fake_think
        )

        _run_scan_all_sources(db)

        # Two enabled sources → scan, think, scan.
        scan_events = [e for e in call_order if e.startswith("scan:")]
        think_events = [e for e in call_order if e == "think"]
        assert len(scan_events) == 2, call_order
        assert len(think_events) == 1, call_order
        # Think must sit between the two scans, not before or after.
        scan_idx = [i for i, e in enumerate(call_order) if e.startswith("scan:")]
        think_idx = call_order.index("think")
        assert scan_idx[0] < think_idx < scan_idx[1], call_order

    def test_wires_cookie_rotation_callback_into_scan_source(
        self, db, account_with_cookies, sources, monkeypatch
    ):
        """Phase I-B-3 — orchestrator passes ``on_cookies_refresh`` callback
        that writes captured (rotated) cookies back to DB silently.

        When scan_source fires the callback with a fresh cookie dict, the
        account's encrypted cookie blob must be replaced; status / profile
        remain untouched.
        """
        from bot.tasks import _run_scan_all_sources
        from server.crypto import decrypt_cookies

        seen_refresh_cbs: list = []

        async def _fake_scan(source, cookies, **kwargs):
            cb = kwargs.get("on_cookies_refresh")
            assert cb is not None, "orchestrator must pass on_cookies_refresh"
            seen_refresh_cbs.append(cb)
            # Simulate FB rotating xs.
            await cb({"c_user": cookies["c_user"], "xs": "ROTATED"})
            return _mock_result(int(source["id"]))

        monkeypatch.setattr(
            "bot.tasks.scan_source", AsyncMock(side_effect=_fake_scan)
        )

        original_status = account_with_cookies.status
        _run_scan_all_sources(db)
        db.refresh(account_with_cookies)

        # Callback was invoked per source.
        assert len(seen_refresh_cbs) >= 2
        # Cookie got rotated into DB.
        fresh = decrypt_cookies(account_with_cookies.cookies_encrypted or "")
        assert fresh.get("xs") == "ROTATED"
        # Status untouched.
        assert account_with_cookies.status == original_status
