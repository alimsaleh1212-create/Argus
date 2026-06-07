"""Baseline migration: schema_marker table.

Revision ID: 0001
Revises: (none — this is the base migration)
Create Date: 2026-06-06

The schema_marker table is a one-row infra marker that proves Alembic
has been run and the migration pipeline is exercised from day one
(FR-015, FR-016). Later specs add their own tables via new migrations.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "schema_marker",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=False),
        sa.Column("component", sa.String(length=64), nullable=False),
        sa.Column("migrated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.execute("INSERT INTO schema_marker (id, component) VALUES (1, 'platform-infra')")


def downgrade() -> None:
    op.drop_table("schema_marker")
