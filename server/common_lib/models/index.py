"""The code index: embeddings and obfuscated metadata only. There is deliberately no
column for plaintext code, file names or symbols."""

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import CHAR, CheckConstraint, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from common_lib.models.base import Base, created_at, hmac_checks, hmac_hex, updated_at, uuid_pk

# Qwen3-Embedding-0.6B (ADR-0002). Changing it needs a migration and a re-index.
EMBEDDING_DIMENSION = 1024


class CodeChunk(Base):
    __tablename__ = "code_chunks"
    __table_args__ = (
        CheckConstraint("line_end >= line_start", name="line_range"),
        *hmac_checks("obfuscated_filename", "encrypted_chunk_hash"),
        UniqueConstraint("repository_id", "encrypted_chunk_hash", "obfuscated_filename"),
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


class MerkleNode(Base):
    __tablename__ = "merkle_nodes"
    __table_args__ = (
        Index("idx_merkle_nodes_parent", "repository_id", "parent_id"),
        *hmac_checks("node_id", "parent_id", "hash"),
    )

    repository_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), primary_key=True
    )
    # HMAC of a path or chunk position.
    node_id: Mapped[str] = mapped_column(CHAR(64), primary_key=True)
    parent_id: Mapped[str | None] = mapped_column(CHAR(64))
    hash: Mapped[hmac_hex]
    is_leaf: Mapped[bool]
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("code_chunks.id", ondelete="SET NULL")
    )
    updated_at: Mapped[updated_at]
