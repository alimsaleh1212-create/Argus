"""Create incidents table for the ingestion pipeline.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-08

Stores every accepted Wazuh alert as an Incident row.
raw_alert / normalized_event / evidence are JSONB (validated by Pydantic at the boundary).
Reversible: downgrade drops indexes then the table.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "incidents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("severity", sa.Text, nullable=False),
        sa.Column("correlation_id", sa.Text, nullable=False),
        sa.Column("dedup_fingerprint", sa.Text, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column(
            "raw_alert",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("normalized_event", postgresql.JSONB, nullable=True),
        sa.Column("evidence", postgresql.JSONB, nullable=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_incidents_status", "incidents", ["status"])
    op.create_index("ix_incidents_dedup_fingerprint", "incidents", ["dedup_fingerprint"])
    op.create_index("ix_incidents_correlation_id", "incidents", ["correlation_id"])


def downgrade() -> None:
    op.drop_index("ix_incidents_correlation_id", table_name="incidents")
    op.drop_index("ix_incidents_dedup_fingerprint", table_name="incidents")
    op.drop_index("ix_incidents_status", table_name="incidents")
    op.drop_table("incidents")
