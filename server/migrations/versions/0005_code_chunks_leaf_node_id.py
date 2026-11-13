"""`code_chunks.leaf_node_id`: rows are keyed by the Merkle leaf's position id, which
replaces the unique `(repository_id, encrypted_chunk_hash, obfuscated_filename)`
(ADR-0020), so identical chunks in one file stay separate rows.

Written by hand. Nothing writes `code_chunks` before this revision (the indexer arrives
in slice 8), so the table is emptied first and the new column needs no default.

Revision ID: 0005
Revises: 0004
Created: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Longer than Postgres's 63 characters: it truncates the name the same way on every use.
OLD_UNIQUE = "uq_code_chunks_repository_id_encrypted_chunk_hash_obfuscated_filename"


def upgrade() -> None:
    op.execute("DELETE FROM code_chunks")
    op.add_column("code_chunks", sa.Column("leaf_node_id", sa.CHAR(length=64), nullable=False))
    op.create_check_constraint(
        op.f("ck_code_chunks_leaf_node_id_hex"), "code_chunks", "leaf_node_id ~ '^[0-9a-f]{64}$'"
    )
    op.drop_constraint(op.f(OLD_UNIQUE), "code_chunks", type_="unique")
    op.create_unique_constraint(
        op.f("uq_code_chunks_repository_id_leaf_node_id"),
        "code_chunks",
        ["repository_id", "leaf_node_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("uq_code_chunks_repository_id_leaf_node_id"), "code_chunks", type_="unique"
    )
    op.execute("DELETE FROM code_chunks")
    op.create_unique_constraint(
        op.f(OLD_UNIQUE),
        "code_chunks",
        ["repository_id", "encrypted_chunk_hash", "obfuscated_filename"],
    )
    op.drop_constraint(op.f("ck_code_chunks_leaf_node_id_hex"), "code_chunks", type_="check")
    op.drop_column("code_chunks", "leaf_node_id")
