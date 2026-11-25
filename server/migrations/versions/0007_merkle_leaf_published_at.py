"""`merkle_nodes.published_at`: when the gateway last handed a pending leaf's chunk to
the indexer (slice 8c3c). An upload of a leaf published within the settle window is
acknowledged without a second publish, and the re-request leaves it alone until then.
Only a pending leaf has one.

Written by hand; the column is new and empty, so no data moves.

Revision ID: 0007
Revises: 0006
Created: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHECK = "published_only_pending"


def upgrade() -> None:
    op.add_column(
        "merkle_nodes", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        op.f(f"ck_merkle_nodes_{CHECK}"),
        "merkle_nodes",
        "published_at IS NULL OR index_state = 'pending'",
    )


def downgrade() -> None:
    op.drop_constraint(op.f(f"ck_merkle_nodes_{CHECK}"), "merkle_nodes", type_="check")
    op.drop_column("merkle_nodes", "published_at")
