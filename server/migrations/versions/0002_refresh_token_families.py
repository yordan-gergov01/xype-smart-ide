"""Refresh-token families (ADR-0014): `family_id` and the family's absolute expiry.

Written by hand. No refresh token was issued before this revision (the endpoints arrive
with it), so the table is emptied first and the new columns need no default.

Revision ID: 0002
Revises: 0001
Created: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DELETE FROM refresh_tokens")
    op.add_column("refresh_tokens", sa.Column("family_id", sa.UUID(), nullable=False))
    op.add_column(
        "refresh_tokens",
        sa.Column("family_expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_refresh_tokens_family", "refresh_tokens", ["family_id"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_refresh_tokens_family", table_name="refresh_tokens")
    op.drop_column("refresh_tokens", "family_expires_at")
    op.drop_column("refresh_tokens", "family_id")
