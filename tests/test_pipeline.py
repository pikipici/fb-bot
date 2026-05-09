"""Tests for pipeline module."""

import pytest
from datetime import datetime, timezone, timedelta

from bot.modules.pipeline import Pipeline


@pytest.fixture
def pipeline():
    return Pipeline()


class TestProcessPost:
    def test_good_post_queued(self, pipeline):
        post = {
            "fb_post_id": "good1",
            "text": "Saya cari jasa desain untuk project baru",
            "likes": 20,
            "comments": 10,
            "shares": 5,
            "timestamp": datetime.now(timezone.utc) - timedelta(hours=2),
            "language": "id",
        }
        result = pipeline.process_post(post)
        assert result["status"] == "QUEUED"
        assert result["score"] > 0

    def test_low_engagement_filtered(self, pipeline):
        post = {
            "fb_post_id": "low1",
            "text": "Cari jasa desain",
            "likes": 0,
            "comments": 0,
            "shares": 0,
            "timestamp": datetime.now(timezone.utc),
            "language": "id",
        }
        result = pipeline.process_post(post)
        assert result["status"] == "FILTERED_OUT"
        assert result["filter_reason"] == "low_engagement"

    def test_no_keyword_filtered(self, pipeline):
        post = {
            "fb_post_id": "nokw1",
            "text": "Cuaca hari ini sangat cerah dan indah",
            "likes": 50,
            "comments": 20,
            "shares": 10,
            "timestamp": datetime.now(timezone.utc),
            "language": "id",
        }
        result = pipeline.process_post(post)
        assert result["status"] == "FILTERED_OUT"
        assert result["filter_reason"] == "no_keyword_match"

    def test_duplicate_filtered(self, pipeline):
        post = {
            "fb_post_id": "dup1",
            "text": "Butuh jasa desain logo",
            "likes": 10,
            "comments": 5,
            "shares": 2,
            "timestamp": datetime.now(timezone.utc),
            "language": "id",
        }
        # First pass
        result1 = pipeline.process_post(post)
        # Second pass (same fb_post_id)
        result2 = pipeline.process_post(post)
        assert result2["status"] == "FILTERED_OUT"
        assert result2["filter_reason"] == "duplicate"

    def test_high_risk_filtered(self, pipeline):
        post = {
            "fb_post_id": "risk1",
            "text": "Berita politik hoax sara terbaru",
            "likes": 100,
            "comments": 50,
            "shares": 30,
            "timestamp": datetime.now(timezone.utc),
            "language": "id",
        }
        result = pipeline.process_post(post)
        assert result["status"] == "FILTERED_OUT"
        assert result["filter_reason"] == "high_risk"


class TestProcessBatch:
    def test_batch_processing(self, pipeline):
        posts = [
            {
                "fb_post_id": "b1",
                "text": "Cari jasa desain website",
                "likes": 30,
                "comments": 10,
                "shares": 5,
                "timestamp": datetime.now(timezone.utc) - timedelta(hours=1),
                "language": "id",
            },
            {
                "fb_post_id": "b2",
                "text": "Jual mobil bekas murah",
                "likes": 50,
                "comments": 20,
                "shares": 10,
                "timestamp": datetime.now(timezone.utc),
                "language": "id",
            },
            {
                "fb_post_id": "b3",
                "text": "Butuh jasa editing video",
                "likes": 15,
                "comments": 5,
                "shares": 2,
                "timestamp": datetime.now(timezone.utc) - timedelta(hours=3),
                "language": "id",
            },
        ]
        result = pipeline.process_batch(posts)
        assert result["total"] == 3
        assert result["queued"] + result["filtered"] == 3
        assert result["queued"] >= 1  # At least the keyword-matching ones

    def test_empty_batch(self, pipeline):
        result = pipeline.process_batch([])
        assert result["total"] == 0
        assert result["queued"] == 0
        assert result["filtered"] == 0
