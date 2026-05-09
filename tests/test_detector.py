"""Tests for detector module."""

import pytest

from bot.modules.detector import Detector


@pytest.fixture
def detector():
    return Detector()


class TestKeywordMatching:
    def test_matches_whitelist(self, detector):
        post = {"text": "Saya cari jasa desain logo", "fb_post_id": "p1"}
        result = detector.detect(post)
        assert result["matched_keywords"] >= 1
        assert "cari jasa" in result["matched_keyword_list"]

    def test_no_match(self, detector):
        post = {"text": "Cuaca hari ini cerah sekali", "fb_post_id": "p2"}
        result = detector.detect(post)
        assert result["matched_keywords"] == 0

    def test_multiple_matches(self, detector):
        post = {"text": "Butuh jasa dan minta rekomendasi", "fb_post_id": "p3"}
        result = detector.detect(post)
        assert result["matched_keywords"] >= 2

    def test_case_insensitive(self, detector):
        post = {"text": "CARI JASA desain", "fb_post_id": "p4"}
        result = detector.detect(post)
        assert result["matched_keywords"] >= 1


class TestRiskDetection:
    def test_no_risk(self, detector):
        post = {"text": "Butuh jasa desain logo", "fb_post_id": "r1"}
        result = detector.detect(post)
        assert result["risk_tags"] == []

    def test_single_risk(self, detector):
        post = {"text": "Diskusi politik hari ini", "fb_post_id": "r2"}
        result = detector.detect(post)
        assert "politik" in result["risk_tags"]

    def test_multiple_risks(self, detector):
        post = {"text": "Berita hoax tentang politik", "fb_post_id": "r3"}
        result = detector.detect(post)
        assert "politik" in result["risk_tags"]
        assert "hoax" in result["risk_tags"]


class TestLanguageFilter:
    def test_supported_language(self, detector):
        post = {"text": "test", "language": "id", "fb_post_id": "l1"}
        result = detector.detect(post)
        assert result["language_ok"] is True

    def test_default_language(self, detector):
        post = {"text": "test", "fb_post_id": "l2"}
        result = detector.detect(post)
        assert result["language"] == "id"
        assert result["language_ok"] is True


class TestDuplicateDetection:
    def test_first_occurrence_not_duplicate(self, detector):
        post = {"text": "test", "fb_post_id": "dup1"}
        result = detector.detect(post)
        assert result["is_duplicate"] is False

    def test_second_occurrence_is_duplicate(self, detector):
        post = {"text": "test", "fb_post_id": "dup2"}
        detector.detect(post)
        result = detector.detect(post)
        assert result["is_duplicate"] is True

    def test_load_seen_ids(self, detector):
        detector.load_seen_ids(["existing1", "existing2"])
        post = {"text": "test", "fb_post_id": "existing1"}
        result = detector.detect(post)
        assert result["is_duplicate"] is True


class TestFilterDecision:
    def test_no_keyword_filtered(self, detector):
        post = {
            "text": "Random text",
            "fb_post_id": "f1",
            "matched_keywords": 0,
            "risk_tags": [],
            "language_ok": True,
            "is_duplicate": False,
        }
        filtered, reason = detector.should_filter_out(post)
        assert filtered is True
        assert reason == "no_keyword_match"

    def test_duplicate_filtered(self, detector):
        post = {
            "text": "test",
            "fb_post_id": "f2",
            "matched_keywords": 1,
            "risk_tags": [],
            "language_ok": True,
            "is_duplicate": True,
        }
        filtered, reason = detector.should_filter_out(post)
        assert filtered is True
        assert reason == "duplicate"

    def test_high_risk_filtered(self, detector):
        post = {
            "text": "test",
            "fb_post_id": "f3",
            "matched_keywords": 1,
            "risk_tags": ["politik", "sara", "hoax"],
            "language_ok": True,
            "is_duplicate": False,
        }
        filtered, reason = detector.should_filter_out(post)
        assert filtered is True
        assert reason == "high_risk"

    def test_good_post_not_filtered(self, detector):
        post = {
            "text": "test",
            "fb_post_id": "f4",
            "matched_keywords": 2,
            "risk_tags": [],
            "language_ok": True,
            "is_duplicate": False,
        }
        filtered, reason = detector.should_filter_out(post)
        assert filtered is False
        assert reason == ""
