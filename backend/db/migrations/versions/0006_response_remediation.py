"""Add approval_requests and audit_log tables for response/remediation (#10).

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-10

New persistence for the human-in-the-loop approval interrupt and append-only audit ledger.
The incidents table is unchanged — new disposition values are plain text in the existing column.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approval_requests",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("incident_id", sa.UUID, sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("plan_id", sa.Text, nullable=False),
        sa.Column("pending_actions", sa.JSON, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("rationale", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'pending'")),
        sa.Column(
            "deadline_at",
            sa.TIMESTAMP(timezone=False),
            nullable=False,
        ),
        sa.Column("decided_by", sa.Text, nullable=True),
        sa.Column("decided_at", sa.TIMESTAMP(timezone=False), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_approval_requests_incident_id", "approval_requests", ["incident_id"])
    op.create_index("ix_approval_requests_status", "approval_requests", ["status"])
    # Sweeper index: pending rows with elapsed deadline
    op.create_index(
        "ix_approval_requests_pending_deadline",
        "approval_requests",
        ["status", "deadline_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    # Partial unique: one pending row per incident (v1 parks once)
    op.create_index(
        "uq_approval_requests_incident_pending",
        "approval_requests",
        ["incident_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("incident_id", sa.UUID, sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("actor", sa.Text, nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("target", sa.Text, nullable=True),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column("idempotency_key", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_audit_log_incident_id", "audit_log", ["incident_id"])
    # Partial unique: each idempotency_key maps to at most one applied row (blocks double-execution)
    op.create_index(
        "uq_audit_applied_idem",
        "audit_log",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("outcome = 'applied'"),
    )


def downgrade() -> None:
    op.drop_index("uq_audit_applied_idem", table_name="audit_log")
    op.drop_index("ix_audit_log_incident_id", table_name="audit_log")
    op.drop_table("audit_log")

    op.drop_index("uq_approval_requests_incident_pending", table_name="approval_requests")
    op.drop_index("ix_approval_requests_pending_deadline", table_name="approval_requests")
    op.drop_index("ix_approval_requests_status", table_name="approval_requests")
    op.drop_index("ix_approval_requests_incident_id", table_name="approval_requests")
    op.drop_table("approval_requests")
