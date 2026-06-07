"""Add trace_spans table for the OTel-backed trace store.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-07

Stores per-incident span trees queryable by correlation_id.
Attributes column contains pre-redacted JSONB (TRACE boundary).
Reversible: downgrade drops the table.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trace_spans",
        sa.Column("span_id", sa.Text, primary_key=True),
        sa.Column("trace_id", sa.Text, nullable=False),
        sa.Column("parent_span_id", sa.Text, nullable=True),
        sa.Column("correlation_id", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("llm_model", sa.Text, nullable=True),
        sa.Column("tokens_in", sa.Integer, nullable=True),
        sa.Column("tokens_out", sa.Integer, nullable=True),
        sa.Column(
            "attributes",
            sa.dialects.postgresql.JSONB,
            nullable=False,
            server_default="'{}'::jsonb",
        ),
        sa.Column("error_message", sa.Text, nullable=True),
    )
    op.create_index("ix_trace_spans_correlation_id", "trace_spans", ["correlation_id"])
    op.create_index("ix_trace_spans_trace_id", "trace_spans", ["trace_id"])
    op.create_index(
        "ix_trace_spans_trace_parent",
        "trace_spans",
        ["trace_id", "parent_span_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_trace_spans_trace_parent", table_name="trace_spans")
    op.drop_index("ix_trace_spans_trace_id", table_name="trace_spans")
    op.drop_index("ix_trace_spans_correlation_id", table_name="trace_spans")
    op.drop_table("trace_spans")
