"""Model tests for layer 1+2 schema (cookie session, sources, trending, templates, history)."""
from __future__ import annotations

from server.models import (
    CommentHistory,
    CommentTemplate,
    FBAccount,
    Source,
    TrendingPost,
)


class TestFBAccountCookieColumns:
    def test_cookies_encrypted_column(self):
        assert hasattr(FBAccount, "cookies_encrypted")

    def test_fb_user_id_column(self):
        assert hasattr(FBAccount, "fb_user_id")

    def test_fb_name_column(self):
        assert hasattr(FBAccount, "fb_name")

    def test_fb_profile_pic_url_column(self):
        assert hasattr(FBAccount, "fb_profile_pic_url")

    def test_cookies_expired_at_column(self):
        assert hasattr(FBAccount, "cookies_expired_at")

    def test_email_and_password_nullable(self):
        # Cookie-connected accounts don't need credentials
        email_col = FBAccount.__table__.columns["email_encrypted"]
        password_col = FBAccount.__table__.columns["password_encrypted"]
        assert email_col.nullable is True
        assert password_col.nullable is True


class TestSourceModel:
    def test_tablename(self):
        assert Source.__tablename__ == "sources"

    def test_required_columns(self):
        cols = Source.__table__.columns
        for name in (
            "id",
            "type",
            "label",
            "url",
            "fb_entity_id",
            "keywords_include",
            "keywords_exclude",
            "enabled",
            "last_scanned_at",
            "created_at",
        ):
            assert name in cols, f"Source missing column {name}"

    def test_type_not_nullable(self):
        assert Source.__table__.columns["type"].nullable is False

    def test_label_not_nullable(self):
        assert Source.__table__.columns["label"].nullable is False


class TestTrendingPostModel:
    def test_tablename(self):
        assert TrendingPost.__tablename__ == "trending_posts"

    def test_required_columns(self):
        cols = TrendingPost.__table__.columns
        for name in (
            "id",
            "fb_post_id",
            "source_id",
            "author_name",
            "author_fb_id",
            "text_snippet",
            "post_url",
            "thumbnail_url",
            "likes",
            "comments",
            "shares",
            "reactions_total",
            "score",
            "velocity",
            "post_timestamp",
            "collected_at",
            "status",
        ):
            assert name in cols, f"TrendingPost missing column {name}"

    def test_fb_post_id_unique(self):
        assert TrendingPost.__table__.columns["fb_post_id"].unique is True

    def test_source_id_fk(self):
        fks = list(TrendingPost.__table__.columns["source_id"].foreign_keys)
        assert len(fks) == 1
        assert fks[0].column.table.name == "sources"
        assert fks[0].ondelete == "CASCADE"


class TestCommentTemplateModel:
    def test_tablename(self):
        assert CommentTemplate.__tablename__ == "comment_templates"

    def test_template_text_not_nullable(self):
        assert (
            CommentTemplate.__table__.columns["template_text"].nullable
            is False
        )

    def test_has_is_active(self):
        assert "is_active" in CommentTemplate.__table__.columns


class TestCommentHistoryModel:
    def test_tablename(self):
        assert CommentHistory.__tablename__ == "comment_history"

    def test_required_columns(self):
        cols = CommentHistory.__table__.columns
        for name in (
            "id",
            "trending_post_id",
            "user_id",
            "comment_text",
            "fb_comment_id",
            "status",
            "error_message",
            "sent_at",
        ):
            assert name in cols, f"CommentHistory missing column {name}"

    def test_trending_post_id_cascade_fk(self):
        fks = list(
            CommentHistory.__table__.columns["trending_post_id"].foreign_keys
        )
        assert len(fks) == 1
        assert fks[0].column.table.name == "trending_posts"
        assert fks[0].ondelete == "CASCADE"

    def test_user_id_set_null_fk(self):
        fks = list(CommentHistory.__table__.columns["user_id"].foreign_keys)
        assert len(fks) == 1
        assert fks[0].column.table.name == "users"
        assert fks[0].ondelete == "SET NULL"

    def test_status_not_nullable(self):
        assert CommentHistory.__table__.columns["status"].nullable is False

    def test_comment_text_not_nullable(self):
        assert (
            CommentHistory.__table__.columns["comment_text"].nullable is False
        )
