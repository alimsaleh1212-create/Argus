"""Add acknowledged_at / acknowledged_by to incidents (operator acknowledge action).

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-18

Additive only — nullable columns. Status/disposition unchanged.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "incidents",
        sa.Column("acknowledged_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column("incidents", sa.Column("acknowledged_by", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("incidents", "acknowledged_by")
    op.drop_column("incidents", "acknowledged_at")
