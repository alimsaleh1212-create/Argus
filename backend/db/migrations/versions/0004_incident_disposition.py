"""Add nullable disposition column to incidents.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-08

Adds a single nullable `disposition text` column carrying the fine-grained
terminal reason (e.g. auto_resolved_noise, escalated_step_cap). Status stays
`text` — the new lifecycle values need no DDL. Reversible: downgrade drops
the column.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("incidents", sa.Column("disposition", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("incidents", "disposition")
