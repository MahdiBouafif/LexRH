"""documents: store the parsed article content (number, text id, hierarchy, text, nota)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("num", sa.String(32), nullable=True))
    op.add_column("documents", sa.Column("text_id", sa.String(24), nullable=True))
    op.add_column(
        "documents",
        sa.Column(
            "hierarchy",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column("documents", sa.Column("text", sa.Text, nullable=False, server_default=""))
    op.add_column("documents", sa.Column("nota", sa.Text, nullable=False, server_default=""))
    op.create_index("ix_documents_text_id", "documents", ["text_id"])


def downgrade() -> None:
    op.drop_index("ix_documents_text_id", table_name="documents")
    for column in ("nota", "text", "hierarchy", "text_id", "num"):
        op.drop_column("documents", column)
