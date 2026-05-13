"""browser fingerprint fields on fb_accounts

Revision ID: 005
Revises: 004
Create Date: 2026-05-13

Phase I-A — pin per-account browser fingerprint (UA + viewport) di DB.
Null default biar existing row aman; field di-assign lazy lewat
``FBAccountService.ensure_fingerprint`` pas scan/send pertama kali.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("fb_accounts") as batch_op:
        batch_op.add_column(
            sa.Column("browser_ua", sa.String(length=300), nullable=True)
        )
        batch_op.add_column(
            sa.Column("viewport_w", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("viewport_h", sa.Integer(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("fb_accounts") as batch_op:
        batch_op.drop_column("viewport_h")
        batch_op.drop_column("viewport_w")
        batch_op.drop_column("browser_ua")
