"""scanner_runs audit table

Revision ID: 004
Revises: 003
Create Date: 2026-05-10

Adds ``scanner_runs`` — one row per ``scan_all_sources`` execution.
Used by ``GET /api/v1/scanner/status`` to show the last scan timestamp
and by ``POST /scanner/run-now`` to record manual triggers.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scanner_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(length=100), nullable=True),
        sa.Column(
            "trigger",
            sa.String(length=20),
            nullable=False,
            server_default="beat",
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="running",
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enabled_sources", sa.Integer(), server_default="0"),
        sa.Column("successful_scans", sa.Integer(), server_default="0"),
        sa.Column("scan_errors", sa.Integer(), server_default="0"),
        sa.Column("inserted", sa.Integer(), server_default="0"),
        sa.Column("updated", sa.Integer(), server_default="0"),
        sa.Column("skipped", sa.Integer(), server_default="0"),
        sa.Column("aborted_reason", sa.String(length=50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_scanner_runs_task_id", "scanner_runs", ["task_id"], unique=False
    )
    op.create_index(
        "ix_scanner_runs_status", "scanner_runs", ["status"], unique=False
    )
    op.create_index(
        "ix_scanner_runs_started_at",
        "scanner_runs",
        ["started_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_scanner_runs_started_at", table_name="scanner_runs")
    op.drop_index("ix_scanner_runs_status", table_name="scanner_runs")
    op.drop_index("ix_scanner_runs_task_id", table_name="scanner_runs")
    op.drop_table("scanner_runs")
