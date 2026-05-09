"""Tests for Collector module."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.modules.circuit_breaker import CircuitBreaker
from bot.modules.collector import Collector, CollectorResult, BlockDetectedError
from bot.modules.parser import Parser
from bot.modules.rate_guard import RateGuard


@pytest.fixture
def rate_guard():
    return RateGuard({
        "global": {"max_requests_per_minute": 60, "max_requests_per_hour": 1000},
        "per_target": {"default": {"min_interval_seconds": 0}},
    })


@pytest.fixture
def circuit_breaker():
    return CircuitBreaker()


@pytest.fixture
def collector(rate_guard, circuit_breaker):
    return Collector(
        rate_guard=rate_guard,
        circuit_breaker=circuit_breaker,
        parser=Parser(),
    )


@pytest.fixture
def sample_target():
    return {
        "id": "test_group",
        "name": "Test Group",
        "type": "group",
        "url": "https://www.facebook.com/groups/test",
        "mode": "scrape_public",
        "priority": 10,
        "cooldown_minutes": 30,
        "max_posts_per_run": 50,
    }


@pytest.fixture
def api_target():
    return {
        "id": "api_page",
        "name": "API Page",
        "type": "page",
        "url": "https://www.facebook.com/apipage",
        "mode": "api_first",
        "fb_id": "123456789",
        "max_posts_per_run": 25,
    }


class TestCollectorResult:
    def test_success_result(self):
        r = CollectorResult("t1", [{"fb_post_id": "1"}], success=True)
        assert r.target_id == "t1"
        assert len(r.posts) == 1
        assert r.success is True
        assert r.blocked is False

    def test_failure_result(self):
        r = CollectorResult("t2", [], success=False, error="timeout", blocked=False)
        assert r.success is False
        assert r.error == "timeout"

    def test_blocked_result(self):
        r = CollectorResult("t3", [], success=False, error="captcha", blocked=True)
        assert r.blocked is True

    def test_repr(self):
        r = CollectorResult("t1", [{"a": 1}, {"b": 2}])
        assert "t1" in repr(r)
        assert "posts=2" in repr(r)


class TestCollectorCircuitBreaker:
    @pytest.mark.asyncio
    async def test_suspended_target_skipped(self, sample_target):
        cb = CircuitBreaker(failure_threshold=1, degraded_threshold=2)
        cb.record_failure("test_group")
        cb.record_failure("test_group")

        collector = Collector(circuit_breaker=cb)
        result = await collector.collect_target(sample_target)

        assert result.success is False
        assert result.error == "suspended"
        assert result.posts == []

    @pytest.mark.asyncio
    async def test_success_records_to_circuit_breaker(self, sample_target, rate_guard):
        cb = CircuitBreaker()
        collector = Collector(rate_guard=rate_guard, circuit_breaker=cb)

        # Mock scrape to return posts
        mock_posts = [{"fb_post_id": "1", "text_snippet": "test"}]
        collector._collect_via_scrape = AsyncMock(return_value=mock_posts)

        result = await collector.collect_target(sample_target)
        assert result.success is True
        assert cb.get_status("test_group").value == "ACTIVE"

    @pytest.mark.asyncio
    async def test_failure_records_to_circuit_breaker(self, sample_target, rate_guard):
        cb = CircuitBreaker()
        collector = Collector(rate_guard=rate_guard, circuit_breaker=cb)

        collector._collect_via_scrape = AsyncMock(side_effect=Exception("network error"))

        result = await collector.collect_target(sample_target)
        assert result.success is False
        assert len(cb._failures.get("test_group", [])) == 1


class TestCollectorRateGuard:
    @pytest.mark.asyncio
    async def test_rate_limited_target_skipped(self, sample_target, circuit_breaker):
        rg = RateGuard({
            "global": {"max_requests_per_minute": 60, "max_requests_per_hour": 1000},
            "per_target": {"default": {"min_interval_seconds": 9999}},
        })
        # First call reserves the slot
        rg.check_and_reserve("test_group")

        collector = Collector(rate_guard=rg, circuit_breaker=circuit_breaker)
        result = await collector.collect_target(sample_target)

        assert result.success is False
        assert result.error == "rate_limited"


class TestCollectorScrapeMode:
    @pytest.mark.asyncio
    async def test_scrape_mode_called(self, collector, sample_target):
        collector._collect_via_scrape = AsyncMock(return_value=[
            {"fb_post_id": "p1", "text_snippet": "Hello"}
        ])

        result = await collector.collect_target(sample_target)
        assert result.success is True
        assert len(result.posts) == 1
        collector._collect_via_scrape.assert_called_once_with(sample_target)

    @pytest.mark.asyncio
    async def test_block_detected_sets_blocked_flag(self, collector, sample_target):
        collector._collect_via_scrape = AsyncMock(
            side_effect=BlockDetectedError("captcha detected")
        )

        result = await collector.collect_target(sample_target)
        assert result.success is False
        assert result.blocked is True
        assert "captcha" in result.error


class TestCollectorApiMode:
    @pytest.mark.asyncio
    async def test_api_mode_called(self, collector, api_target):
        collector._collect_via_api = AsyncMock(return_value=[
            {"fb_post_id": "api_1", "text_snippet": "API post"}
        ])

        result = await collector.collect_target(api_target)
        assert result.success is True
        assert len(result.posts) == 1
        collector._collect_via_api.assert_called_once_with(api_target)

    @pytest.mark.asyncio
    async def test_api_fallback_to_scrape_when_no_token(self, api_target, rate_guard, circuit_breaker):
        collector = Collector(
            rate_guard=rate_guard,
            circuit_breaker=circuit_breaker,
            config={},  # No graph_api_token
        )
        # Mock both methods
        collector._collect_via_scrape = AsyncMock(return_value=[{"fb_post_id": "s1"}])

        # _collect_via_api will call _collect_via_scrape internally when no token
        result = await collector.collect_target(api_target)
        assert result.success is True


class TestBlockDetection:
    def test_check_block_signals_raises(self, collector):
        with pytest.raises(BlockDetectedError):
            collector._check_block_signals("<html>Please complete the captcha</html>")

    def test_check_block_signals_checkpoint(self, collector):
        with pytest.raises(BlockDetectedError):
            collector._check_block_signals("<html>checkpoint required</html>")

    def test_no_block_signal_passes(self, collector):
        # Should not raise
        collector._check_block_signals("<html><body>Normal page content</body></html>")


class TestDeduplication:
    def test_dedup_by_url(self, collector):
        posts = [
            {"url": "https://fb.com/posts/1", "text": "A"},
            {"url": "https://fb.com/posts/1", "text": "A duplicate"},
            {"url": "https://fb.com/posts/2", "text": "B"},
        ]
        result = collector._deduplicate_raw(posts)
        assert len(result) == 2

    def test_dedup_by_text_when_no_url(self, collector):
        posts = [
            {"url": "", "text": "Same text content here that is long enough"},
            {"url": "", "text": "Same text content here that is long enough"},
            {"url": "", "text": "Different text"},
        ]
        result = collector._deduplicate_raw(posts)
        assert len(result) == 2

    def test_empty_posts(self, collector):
        assert collector._deduplicate_raw([]) == []

    def test_posts_with_no_key_skipped(self, collector):
        posts = [
            {"url": "", "text": ""},
            {"url": "https://fb.com/posts/1", "text": "Valid"},
        ]
        result = collector._deduplicate_raw(posts)
        assert len(result) == 1
