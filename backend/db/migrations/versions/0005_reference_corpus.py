"""Add reference_corpus table for static knowledge docs.

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-10

Adds `reference_corpus` for MITRE technique→mitigation mappings and runbooks.
Retrieval is deterministic keyed/lexical — the `embedding` column is reserved
but left null in v1 (CD1). Idempotent upsert target is (kind, key) unique index.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reference_corpus",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("key", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column(
            "tags",
            sa.ARRAY(sa.Text),
            nullable=False,
            server_default="'{}'",
        ),
        # Reserved for future vector recall — left null in v1 (CD1).
        sa.Column("embedding", sa.Text, nullable=True),
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
    op.create_index(
        "uq_reference_corpus_kind_key",
        "reference_corpus",
        ["kind", "key"],
        unique=True,
    )
    op.create_index(
        "ix_reference_corpus_tags",
        "reference_corpus",
        ["tags"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_reference_corpus_tags", table_name="reference_corpus")
    op.drop_index("uq_reference_corpus_kind_key", table_name="reference_corpus")
    op.drop_table("reference_corpus")
