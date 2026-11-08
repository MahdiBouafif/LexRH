"""documents: index to find the versions of one article quickly

Used by retrieval to keep a single version of each article for a given date.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-08
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_documents_text_num_from", "documents", ["text_id", "num", "valid_from"]
    )


def downgrade() -> None:
    op.drop_index("ix_documents_text_num_from", table_name="documents")
