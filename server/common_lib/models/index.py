"""The code index: embeddings and obfuscated metadata only. There is deliberately no
column for plaintext code, file names or symbols."""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CHAR,
    CheckConstraint,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from common_lib.merkle import LEAF_STATES, REJECTION_REASONS
from common_lib.models.base import Base, created_at, hmac_checks, hmac_hex, updated_at, uuid_pk
from common_lib.vectors import EMBEDDING_DIMENSION


class CodeChunk(Base):
    __tablename__ = "code_chunks"
    __table_args__ = (
        CheckConstraint("line_end >= line_start", name="line_range"),
        *hmac_checks("obfuscated_filename", "encrypted_chunk_hash", "leaf_node_id"),
        # One row per Merkle leaf: identical chunks stay separate rows, and a repeated
        # delivery from the broker updates the same row (ADR-0020).
        UniqueConstraint("repository_id", "leaf_node_id"),
        Index(
            "idx_code_chunks_embedding_hnsw",
            "vector_embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"vector_embedding": "vector_cosine_ops"},
        ),
        Index("idx_code_chunks_repo_file", "repository_id", "obfuscated_filename"),
    )

    id: Mapped[uuid_pk]
    repository_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE")
    )
    # HMAC of the path.
    obfuscated_filename: Mapped[hmac_hex]
    # HMAC of the content (Merkle leaf).
    encrypted_chunk_hash: Mapped[hmac_hex]
    # HMAC of the leaf's position (ADR-0018): the upsert and delete key.
    leaf_node_id: Mapped[hmac_hex]
    line_start: Mapped[int]
    line_end: Mapped[int]
    # function | class | block — never a name.
    chunk_kind: Mapped[str | None]
    language_id: Mapped[str | None]
    embedding_model_id: Mapped[str]
    embedding_model_version: Mapped[str]
    vector_embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSION))
    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]


class ChunkEdge(Base):
    """A call-graph edge between obfuscated symbols (brief §3.4.2)."""

    __tablename__ = "chunk_edges"
    __table_args__ = (
        Index("idx_chunk_edges_callee", "repository_id", "callee_symbol"),
        *hmac_checks("caller_symbol", "callee_symbol"),
    )

    repository_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), primary_key=True
    )
    caller_symbol: Mapped[str] = mapped_column(CHAR(64), primary_key=True)
    callee_symbol: Mapped[str] = mapped_column(CHAR(64), primary_key=True)


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class MerkleNode(Base):
    """A node of a repository's Merkle tree (ADR-0018). A leaf also carries its line
    range and its indexing state (slice 8c2): `pending` from the apply that stored it
    with a new hash until the indexer's result settles it, then `indexed` (its
    `code_chunks` row is `leaf_node_id = node_id`) or `rejected` (a fixed reason, no
    vector). `pending_since` and `request_attempts` drive the server's re-request."""

    __tablename__ = "merkle_nodes"
    __table_args__ = (
        Index("idx_merkle_nodes_parent", "repository_id", "parent_id"),
        Index(
            "idx_merkle_nodes_pending",
            "repository_id",
            "pending_since",
            postgresql_where=text("index_state = 'pending'"),
        ),
        *hmac_checks("node_id", "parent_id", "hash"),
        CheckConstraint(f"index_state IN ({_sql_list(LEAF_STATES)})", name="index_state_known"),
        CheckConstraint("is_leaf = (index_state IS NOT NULL)", name="leaf_has_state"),
        CheckConstraint(
            "is_leaf = (line_start IS NOT NULL AND line_end IS NOT NULL)",
            name="leaf_has_lines",
        ),
        CheckConstraint("line_start >= 1 AND line_end >= line_start", name="line_range"),
        CheckConstraint(
            f"rejection_reason IN ({_sql_list(REJECTION_REASONS)})", name="reason_known"
        ),
        CheckConstraint(
            "CASE WHEN index_state = 'rejected' THEN rejection_reason IS NOT NULL"
            " ELSE rejection_reason IS NULL END",
            name="rejected_has_reason",
        ),
        CheckConstraint(
            "CASE WHEN index_state = 'pending' THEN pending_since IS NOT NULL"
            " ELSE pending_since IS NULL END",
            name="pending_has_since",
        ),
        CheckConstraint("request_attempts >= 0", name="request_attempts_positive"),
    )

    repository_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), primary_key=True
    )
    # HMAC of a path or chunk position.
    node_id: Mapped[str] = mapped_column(CHAR(64), primary_key=True)
    parent_id: Mapped[str | None] = mapped_column(CHAR(64))
    hash: Mapped[hmac_hex]
    is_leaf: Mapped[bool]
    line_start: Mapped[int | None]
    line_end: Mapped[int | None]
    index_state: Mapped[str | None] = mapped_column(String(16))
    rejection_reason: Mapped[str | None] = mapped_column(String(32))
    pending_since: Mapped[datetime | None]
    request_attempts: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    updated_at: Mapped[updated_at]
