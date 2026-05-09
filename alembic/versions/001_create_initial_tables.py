"""create initial tables

Revision ID: 001
Revises: None
Create Date: 2026-05-09

Uses ``sa.true()`` / ``sa.false()`` for Boolean defaults so the migration
runs unchanged on both SQLite and PostgreSQL. ``DateTime(timezone=True)``
preserves timezone info on Postgres; SQLite stores the string verbatim.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(50), unique=True, nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), server_default="viewer"),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
    )

    op.create_table(
        "targets",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("type", sa.String(20), server_default="group"),
        sa.Column("url", sa.String(500), nullable=True),
        sa.Column("mode", sa.String(20), server_default="scrape_public"),
        sa.Column("priority", sa.Integer(), server_default="5"),
        sa.Column("cooldown_minutes", sa.Integer(), server_default="30"),
        sa.Column("max_posts_per_run", sa.Integer(), server_default="50"),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true()),
        sa.Column("health_status", sa.String(20), server_default="ACTIVE"),
        sa.Column("health_score", sa.Float(), server_default="1.0"),
    )

    op.create_table(
        "posts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fb_post_id", sa.String(100), unique=True, nullable=False),
        sa.Column("target_id", sa.String(100), nullable=False),
        sa.Column("url", sa.String(500), nullable=True),
        sa.Column("author_id", sa.String(100), nullable=True),
        sa.Column("text_snippet", sa.Text(), nullable=True),
        sa.Column("language", sa.String(10), server_default="id"),
        sa.Column("likes", sa.Integer(), server_default="0"),
        sa.Column("comments", sa.Integer(), server_default="0"),
        sa.Column("shares", sa.Integer(), server_default="0"),
        sa.Column("score", sa.Float(), server_default="0.0"),
        sa.Column("status", sa.String(20), server_default="QUEUED"),
        sa.Column(
            "collected_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column("post_timestamp", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "drafts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("source_type", sa.String(20), server_default="static"),
        sa.Column("template_id", sa.String(50), nullable=True),
        sa.Column("status", sa.String(30), server_default="PENDING_REVIEW"),
        sa.Column("fingerprint", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
    )

    op.create_table(
        "approvals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("draft_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("edited_text", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("resource_type", sa.String(50), nullable=True),
        sa.Column("resource_id", sa.String(100), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("approvals")
    op.drop_table("drafts")
    op.drop_table("posts")
    op.drop_table("targets")
    op.drop_table("users")
