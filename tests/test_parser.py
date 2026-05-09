"""Tests for Parser module."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from bot.modules.parser import Parser


@pytest.fixture
def parser():
    return Parser()


@pytest.fixture
def sample_target():
    return {
        "id": "fb_group_test",
        "name": "Test Group",
        "type": "group",
        "url": "https://www.facebook.com/groups/test",
        "mode": "scrape_public",
    }


class TestParseScrapedPosts:
    def test_basic_parse(self, parser, sample_target):
        raw = [
            {
                "text": "Butuh jasa desain logo",
                "author_name": "John Doe",
                "url": "https://www.facebook.com/groups/test/posts/123456",
                "likes": 10,
                "comments": 5,
                "shares": 2,
            }
        ]
        results = parser.parse_scraped_posts(raw, sample_target)
        assert len(results) == 1
        post = results[0]
        assert post["fb_post_id"] == "123456"
        assert post["text_snippet"] == "Butuh jasa desain logo"
        assert post["author_name"] == "John Doe"
        assert post["likes"] == 10
        assert post["comments"] == 5
        assert post["shares"] == 2
        assert post["target_id"] == "fb_group_test"
        assert post["source_mode"] == "scrape"

    def test_empty_list(self, parser, sample_target):
        results = parser.parse_scraped_posts([], sample_target)
        assert results == []

    def test_missing_url_generates_synthetic_id(self, parser, sample_target):
        raw = [{"text": "Some post text without URL"}]
        results = parser.parse_scraped_posts(raw, sample_target)
        assert len(results) == 1
        assert results[0]["fb_post_id"].startswith("syn_")

    def test_no_text_no_url_skipped(self, parser, sample_target):
        raw = [{"text": "", "url": ""}]
        results = parser.parse_scraped_posts(raw, sample_target)
        assert results == []

    def test_text_truncated_to_500(self, parser, sample_target):
        raw = [{"text": "x" * 1000, "url": "https://facebook.com/posts/111"}]
        results = parser.parse_scraped_posts(raw, sample_target)
        assert len(results[0]["text_snippet"]) == 500

    def test_malformed_post_skipped(self, parser, sample_target):
        raw = [
            {"text": "Good post", "url": "https://facebook.com/posts/111"},
            None,  # This will cause an exception
        ]
        # Should not crash, just skip bad entries
        results = parser.parse_scraped_posts(raw, sample_target)
        assert len(results) == 1

    def test_permalink_url_extraction(self, parser, sample_target):
        raw = [{"text": "Test", "url": "https://facebook.com/permalink/789012"}]
        results = parser.parse_scraped_posts(raw, sample_target)
        assert results[0]["fb_post_id"] == "789012"

    def test_story_fbid_url_extraction(self, parser, sample_target):
        raw = [{"text": "Test", "url": "https://facebook.com/story.php?story_fbid=456789&id=1"}]
        results = parser.parse_scraped_posts(raw, sample_target)
        assert results[0]["fb_post_id"] == "456789"


class TestParseApiPosts:
    def test_basic_api_parse(self, parser, sample_target):
        api_response = {
            "data": [
                {
                    "id": "100_200",
                    "message": "Looking for a designer",
                    "created_time": "2026-05-08T10:00:00+0000",
                    "from": {"name": "Jane", "id": "12345"},
                    "reactions": {"summary": {"total_count": 50}},
                    "comments": {"summary": {"total_count": 12}},
                    "shares": {"count": 3},
                }
            ]
        }
        results = parser.parse_api_posts(api_response, sample_target)
        assert len(results) == 1
        post = results[0]
        assert post["fb_post_id"] == "100_200"
        assert post["text_snippet"] == "Looking for a designer"
        assert post["author_name"] == "Jane"
        assert post["author_id"] == "12345"
        assert post["likes"] == 50
        assert post["comments"] == 12
        assert post["shares"] == 3
        assert post["source_mode"] == "api"

    def test_empty_api_response(self, parser, sample_target):
        results = parser.parse_api_posts({"data": []}, sample_target)
        assert results == []

    def test_missing_fields_handled(self, parser, sample_target):
        api_response = {
            "data": [
                {
                    "id": "999",
                    "message": "",
                    # No from, reactions, comments, shares
                }
            ]
        }
        results = parser.parse_api_posts(api_response, sample_target)
        assert len(results) == 1
        assert results[0]["likes"] == 0
        assert results[0]["author_name"] == ""

    def test_no_id_skipped(self, parser, sample_target):
        api_response = {"data": [{"message": "No ID post"}]}
        results = parser.parse_api_posts(api_response, sample_target)
        assert results == []


class TestEngagementParsing:
    def test_integer_passthrough(self, parser):
        assert parser._parse_engagement_number(42) == 42

    def test_string_number(self, parser):
        assert parser._parse_engagement_number("150") == 150

    def test_k_suffix(self, parser):
        assert parser._parse_engagement_number("1.2K") == 1200

    def test_m_suffix(self, parser):
        assert parser._parse_engagement_number("2M") == 2000000

    def test_comma_separated(self, parser):
        assert parser._parse_engagement_number("1,500") == 1500

    def test_invalid_returns_zero(self, parser):
        assert parser._parse_engagement_number("abc") == 0
        assert parser._parse_engagement_number(None) == 0
        assert parser._parse_engagement_number([]) == 0


class TestTimestampParsing:
    def test_iso_format(self, parser):
        result = parser._parse_timestamp("2026-05-08T10:00:00+00:00")
        assert "2026-05-08" in result

    def test_relative_minutes(self, parser):
        result = parser._parse_timestamp("5 menit lalu")
        parsed = datetime.fromisoformat(result)
        # Should be roughly 5 minutes ago
        diff = datetime.now(timezone.utc) - parsed
        assert 4 <= diff.total_seconds() / 60 <= 6

    def test_relative_hours(self, parser):
        result = parser._parse_timestamp("2 jam lalu")
        parsed = datetime.fromisoformat(result)
        diff = datetime.now(timezone.utc) - parsed
        assert 1.9 <= diff.total_seconds() / 3600 <= 2.1

    def test_relative_days(self, parser):
        result = parser._parse_timestamp("3 hari lalu")
        parsed = datetime.fromisoformat(result)
        diff = datetime.now(timezone.utc) - parsed
        assert 2.9 <= diff.total_seconds() / 86400 <= 3.1

    def test_just_now(self, parser):
        result = parser._parse_timestamp("baru saja")
        parsed = datetime.fromisoformat(result)
        diff = datetime.now(timezone.utc) - parsed
        assert diff.total_seconds() < 5

    def test_none_returns_now(self, parser):
        result = parser._parse_timestamp(None)
        parsed = datetime.fromisoformat(result)
        diff = datetime.now(timezone.utc) - parsed
        assert diff.total_seconds() < 5

    def test_datetime_object(self, parser):
        dt = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        result = parser._parse_timestamp(dt)
        assert "2026-01-15" in result


class TestLanguageDetection:
    def test_indonesian(self, parser):
        text = "Saya butuh jasa desain yang bisa membantu untuk proyek ini"
        assert parser._detect_language(text) == "id"

    def test_english(self, parser):
        text = "I need a designer who can help with this project for the team"
        assert parser._detect_language(text) == "en"

    def test_empty_defaults_to_id(self, parser):
        assert parser._detect_language("") == "id"

    def test_short_text_defaults_to_id(self, parser):
        assert parser._detect_language("hello") == "id"
