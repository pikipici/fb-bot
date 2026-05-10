"""cookie session + sources + trending + templates + comment history

Revision ID: 003
Revises: 002
Create Date: 2026-05-10

Adds the schema for Layer 1 (trending scanner) and Layer 2 (human-approve
comment drafting):

* ``fb_accounts``: add cookie-session columns (``cookies_encrypted``,
  ``fb_user_id``, ``fb_name``, ``fb_profile_pic_url``,
  ``cookies_expired_at``). Relax ``email_encrypted`` and
  ``password_encrypted`` to nullable — new cookie-connected accounts don't
  require them. Existing rows keep their values.
* ``sources``: scan targets (home feed / group / page) with keyword
  include/exclude filters.
* ``trending_posts``: collected posts that passed the trending threshold,
  scored by engagement velocity + absolute reactions.
* ``comment_templates``: user's promotional comment template(s).
* ``comment_history``: log of comments the human sent via the dashboard.

All DDL uses ``op.batch_alter_table`` where SQLite needs a copy-rename.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- fb_accounts: add cookie-session columns, relax creds ------------
    with op.batch_alter_table("fb_accounts") as batch:
        batch.add_column(
            sa.Column("cookies_encrypted", sa.Text(), nullable=True)
        )
        batch.add_column(
            sa.Column("fb_user_id", sa.String(100), nullable=True)
        )
        batch.add_column(sa.Column("fb_name", sa.String(200), nullable=True))
        batch.add_column(
            sa.Column("fb_profile_pic_url", sa.String(500), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "cookies_expired_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch.alter_column(
            "email_encrypted",
            existing_type=sa.Text(),
            nullable=True,
        )
        batch.alter_column(
            "password_encrypted",
            existing_type=sa.Text(),
            nullable=True,
        )
        batch.create_index("ix_fb_accounts_fb_user_id", ["fb_user_id"])

    # --- sources ----------------------------------------------------------
    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("url", sa.String(500), nullable=True),
        sa.Column("fb_entity_id", sa.String(100), nullable=True),
        sa.Column("keywords_include", sa.Text(), nullable=True),
        sa.Column("keywords_exclude", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true()),
        sa.Column(
            "last_scanned_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
    )
    op.create_index("ix_sources_enabled", "sources", ["enabled"])
    op.create_index("ix_sources_type", "sources", ["type"])

    # --- trending_posts ---------------------------------------------------
    op.create_table(
        "trending_posts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "fb_post_id", sa.String(100), unique=True, nullable=False
        ),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("author_name", sa.String(200), nullable=True),
        sa.Column("author_fb_id", sa.String(100), nullable=True),
        sa.Column("text_snippet", sa.Text(), nullable=True),
        sa.Column("post_url", sa.String(500), nullable=True),
        sa.Column("thumbnail_url", sa.String(500), nullable=True),
        sa.Column("likes", sa.Integer(), server_default="0"),
        sa.Column("comments", sa.Integer(), server_default="0"),
        sa.Column("shares", sa.Integer(), server_default="0"),
        sa.Column("reactions_total", sa.Integer(), server_default="0"),
        sa.Column("score", sa.Float(), server_default="0.0"),
        sa.Column("velocity", sa.Float(), server_default="0.0"),
        sa.Column(
            "post_timestamp", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "collected_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column("status", sa.String(20), server_default="NEW"),
    )
    op.create_index(
        "ix_trending_posts_fb_post_id",
        "trending_posts",
        ["fb_post_id"],
        unique=True,
    )
    op.create_index(
        "ix_trending_posts_source_id", "trending_posts", ["source_id"]
    )
    op.create_index(
        "ix_trending_posts_status", "trending_posts", ["status"]
    )
    op.create_index(
        "ix_trending_posts_score", "trending_posts", ["score"]
    )
    op.create_index(
        "ix_trending_posts_collected_at",
        "trending_posts",
        ["collected_at"],
    )

    # --- comment_templates ------------------------------------------------
    op.create_table(
        "comment_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "name", sa.String(100), server_default="default", nullable=False
        ),
        sa.Column("template_text", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_comment_templates_is_active",
        "comment_templates",
        ["is_active"],
    )

    # --- comment_history --------------------------------------------------
    op.create_table(
        "comment_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "trending_post_id",
            sa.Integer(),
            sa.ForeignKey("trending_posts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("comment_text", sa.Text(), nullable=False),
        sa.Column("fb_comment_id", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_comment_history_trending_post_id",
        "comment_history",
        ["trending_post_id"],
    )
    op.create_index(
        "ix_comment_history_user_id", "comment_history", ["user_id"]
    )
    op.create_index(
        "ix_comment_history_sent_at", "comment_history", ["sent_at"]
    )
    op.create_index(
        "ix_comment_history_status", "comment_history", ["status"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_comment_history_status", table_name="comment_history"
    )
    op.drop_index(
        "ix_comment_history_sent_at", table_name="comment_history"
    )
    op.drop_index(
        "ix_comment_history_user_id", table_name="comment_history"
    )
    op.drop_index(
        "ix_comment_history_trending_post_id", table_name="comment_history"
    )
    op.drop_table("comment_history")

    op.drop_index(
        "ix_comment_templates_is_active", table_name="comment_templates"
    )
    op.drop_table("comment_templates")

    op.drop_index(
        "ix_trending_posts_collected_at", table_name="trending_posts"
    )
    op.drop_index("ix_trending_posts_score", table_name="trending_posts")
    op.drop_index("ix_trending_posts_status", table_name="trending_posts")
    op.drop_index(
        "ix_trending_posts_source_id", table_name="trending_posts"
    )
    op.drop_index(
        "ix_trending_posts_fb_post_id", table_name="trending_posts"
    )
    op.drop_table("trending_posts")

    op.drop_index("ix_sources_type", table_name="sources")
    op.drop_index("ix_sources_enabled", table_name="sources")
    op.drop_table("sources")

    with op.batch_alter_table("fb_accounts") as batch:
        batch.drop_index("ix_fb_accounts_fb_user_id")
        batch.alter_column(
            "password_encrypted",
            existing_type=sa.Text(),
            nullable=False,
        )
        batch.alter_column(
            "email_encrypted",
            existing_type=sa.Text(),
            nullable=False,
        )
        batch.drop_column("cookies_expired_at")
        batch.drop_column("fb_profile_pic_url")
        batch.drop_column("fb_name")
        batch.drop_column("fb_user_id")
        batch.drop_column("cookies_encrypted")
