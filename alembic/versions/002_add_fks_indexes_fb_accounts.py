"""add FKs, indexes, and fb_accounts table

Revision ID: 002
Revises: 001
Create Date: 2026-05-09

This migration is the data-integrity pass that the original 001 missed.
It adds:

* ForeignKey constraints (``posts.target_id``, ``drafts.post_id``,
  ``approvals.draft_id``/``user_id``, ``audit_logs.user_id``) so
  cascade deletes and referential integrity actually work.
* Indexes on every FK column and on the hot-path filters
  (``posts.status``, ``drafts.status``, ``posts.collected_at``,
  ``drafts.created_at``, ``users.role``, ``targets.enabled``).
* The ``fb_accounts`` table (previously referenced by the server layer
  but never created by migration).

All DDL is wrapped in ``op.batch_alter_table`` so SQLite (which cannot
ALTER most columns in place) executes this via the Alembic copy-rename
emulation automatically.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _create_fk(
    batch: "op.BatchOperations",  # type: ignore[name-defined]
    name: str,
    local_col: str,
    remote_table: str,
    remote_col: str,
    ondelete: str,
) -> None:
    batch.create_foreign_key(
        name, remote_table, [local_col], [remote_col], ondelete=ondelete
    )


def upgrade() -> None:
    # --- posts: FK to targets + indexes -----------------------------------
    with op.batch_alter_table("posts") as batch:
        _create_fk(batch, "fk_posts_target_id", "target_id", "targets", "id", "CASCADE")
        batch.create_index("ix_posts_fb_post_id", ["fb_post_id"], unique=True)
        batch.create_index("ix_posts_target_id", ["target_id"])
        batch.create_index("ix_posts_status", ["status"])
        batch.create_index("ix_posts_score", ["score"])
        batch.create_index("ix_posts_collected_at", ["collected_at"])

    # --- drafts: FK to posts + indexes ------------------------------------
    with op.batch_alter_table("drafts") as batch:
        _create_fk(batch, "fk_drafts_post_id", "post_id", "posts", "id", "CASCADE")
        batch.create_index("ix_drafts_post_id", ["post_id"])
        batch.create_index("ix_drafts_status", ["status"])
        batch.create_index("ix_drafts_created_at", ["created_at"])

    # --- approvals: FK to drafts + users + indexes ------------------------
    with op.batch_alter_table("approvals") as batch:
        batch.alter_column(
            "user_id", existing_type=sa.Integer(), nullable=True
        )
        _create_fk(
            batch, "fk_approvals_draft_id", "draft_id", "drafts", "id", "CASCADE"
        )
        _create_fk(
            batch,
            "fk_approvals_user_id",
            "user_id",
            "users",
            "id",
            "SET NULL",
        )
        batch.create_index("ix_approvals_draft_id", ["draft_id"])
        batch.create_index("ix_approvals_user_id", ["user_id"])
        batch.create_index("ix_approvals_created_at", ["created_at"])

    # --- audit_logs: FK to users + indexes --------------------------------
    with op.batch_alter_table("audit_logs") as batch:
        _create_fk(
            batch,
            "fk_audit_logs_user_id",
            "user_id",
            "users",
            "id",
            "SET NULL",
        )
        batch.create_index("ix_audit_logs_user_id", ["user_id"])
        batch.create_index("ix_audit_logs_created_at", ["created_at"])

    # --- users + targets: role / enabled hot-path indexes -----------------
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_index("ix_users_role", "users", ["role"])
    op.create_index("ix_targets_enabled", "targets", ["enabled"])

    # --- fb_accounts table (new) ------------------------------------------
    op.create_table(
        "fb_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("email_encrypted", sa.Text(), nullable=False),
        sa.Column("password_encrypted", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), server_default="ACTIVE"),
        sa.Column("purpose", sa.String(20), server_default="both"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_count", sa.Integer(), server_default="0"),
        sa.Column("total_uses", sa.Integer(), server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
    )
    op.create_index("ix_fb_accounts_status", "fb_accounts", ["status"])


def downgrade() -> None:
    op.drop_index("ix_fb_accounts_status", table_name="fb_accounts")
    op.drop_table("fb_accounts")

    op.drop_index("ix_targets_enabled", table_name="targets")
    op.drop_index("ix_users_role", table_name="users")
    op.drop_index("ix_users_username", table_name="users")

    with op.batch_alter_table("audit_logs") as batch:
        batch.drop_index("ix_audit_logs_created_at")
        batch.drop_index("ix_audit_logs_user_id")
        batch.drop_constraint("fk_audit_logs_user_id", type_="foreignkey")

    with op.batch_alter_table("approvals") as batch:
        batch.drop_index("ix_approvals_created_at")
        batch.drop_index("ix_approvals_user_id")
        batch.drop_index("ix_approvals_draft_id")
        batch.drop_constraint("fk_approvals_user_id", type_="foreignkey")
        batch.drop_constraint("fk_approvals_draft_id", type_="foreignkey")
        batch.alter_column(
            "user_id", existing_type=sa.Integer(), nullable=False
        )

    with op.batch_alter_table("drafts") as batch:
        batch.drop_index("ix_drafts_created_at")
        batch.drop_index("ix_drafts_status")
        batch.drop_index("ix_drafts_post_id")
        batch.drop_constraint("fk_drafts_post_id", type_="foreignkey")

    with op.batch_alter_table("posts") as batch:
        batch.drop_index("ix_posts_collected_at")
        batch.drop_index("ix_posts_score")
        batch.drop_index("ix_posts_status")
        batch.drop_index("ix_posts_target_id")
        batch.drop_index("ix_posts_fb_post_id")
        batch.drop_constraint("fk_posts_target_id", type_="foreignkey")
