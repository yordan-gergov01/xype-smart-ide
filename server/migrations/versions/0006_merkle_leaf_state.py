"""Merkle leaves carry their line range and indexing state (ADR-0018 amendment, slice
8c2): `pending` from the apply that stored a leaf with a new hash until the indexer's
result settles it, then `indexed` or `rejected` with a fixed reason. `pending_since` and
`request_attempts` are for the server's re-request of stale pending leaves (slice 8c3).
The unused `merkle_nodes.chunk_id` goes: the link to a chunk is the leaf id.

Written by hand. No sync has stored a leaf with a vector or a rejection before this
revision (uploads arrive in slice 8c3), so the index is emptied and every repository's
root is cleared: the next sync rebuilds the tree from the client's.

Revision ID: 0006
Revises: 0005
Created: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATES = "'pending', 'indexed', 'rejected'"
REASONS = "'secret_found', 'too_large', 'unscannable', 'unembeddable'"
CHECKS = {
    "index_state_known": f"index_state IN ({STATES})",
    "leaf_has_state": "is_leaf = (index_state IS NOT NULL)",
    "leaf_has_lines": "is_leaf = (line_start IS NOT NULL AND line_end IS NOT NULL)",
    "line_range": "line_start >= 1 AND line_end >= line_start",
    "reason_known": f"rejection_reason IN ({REASONS})",
    "rejected_has_reason": (
        "CASE WHEN index_state = 'rejected' THEN rejection_reason IS NOT NULL"
        " ELSE rejection_reason IS NULL END"
    ),
    "pending_has_since": (
        "CASE WHEN index_state = 'pending' THEN pending_since IS NOT NULL"
        " ELSE pending_since IS NULL END"
    ),
    "request_attempts_positive": "request_attempts >= 0",
}


def _empty_the_index() -> None:
    op.execute("DELETE FROM code_chunks")
    op.execute("DELETE FROM merkle_nodes")
    op.execute("UPDATE repositories SET merkle_root_hash = NULL, last_synced_at = NULL")


def upgrade() -> None:
    _empty_the_index()
    op.drop_constraint(
        op.f("fk_merkle_nodes_chunk_id_code_chunks"), "merkle_nodes", type_="foreignkey"
    )
    op.drop_column("merkle_nodes", "chunk_id")
    op.add_column("merkle_nodes", sa.Column("line_start", sa.Integer(), nullable=True))
    op.add_column("merkle_nodes", sa.Column("line_end", sa.Integer(), nullable=True))
    op.add_column("merkle_nodes", sa.Column("index_state", sa.String(16), nullable=True))
    op.add_column("merkle_nodes", sa.Column("rejection_reason", sa.String(32), nullable=True))
    op.add_column(
        "merkle_nodes", sa.Column("pending_since", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "merkle_nodes",
        sa.Column(
            "request_attempts", sa.SmallInteger(), server_default=sa.text("0"), nullable=False
        ),
    )
    for name, condition in CHECKS.items():
        op.create_check_constraint(op.f(f"ck_merkle_nodes_{name}"), "merkle_nodes", condition)
    op.create_index(
        "idx_merkle_nodes_pending",
        "merkle_nodes",
        ["repository_id", "pending_since"],
        postgresql_where=sa.text("index_state = 'pending'"),
    )


def downgrade() -> None:
    _empty_the_index()
    op.drop_index("idx_merkle_nodes_pending", table_name="merkle_nodes")
    for name in CHECKS:
        op.drop_constraint(op.f(f"ck_merkle_nodes_{name}"), "merkle_nodes", type_="check")
    for column in (
        "request_attempts",
        "pending_since",
        "rejection_reason",
        "index_state",
        "line_end",
        "line_start",
    ):
        op.drop_column("merkle_nodes", column)
    op.add_column("merkle_nodes", sa.Column("chunk_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_merkle_nodes_chunk_id_code_chunks"),
        "merkle_nodes",
        "code_chunks",
        ["chunk_id"],
        ["id"],
        ondelete="SET NULL",
    )
