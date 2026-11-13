"""Embeddings as the indexer, the backend's store and retrieval share them (ADR-0020).
A record carries only the vector, the obfuscated `ChunkRef` and the model."""

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import UUID

from common_lib.domain import ChunkRef, ModelRef, is_hmac_hex
from common_lib.errors import InvalidValueError

# Qwen3-Embedding-0.6B (ADR-0002). Changing it needs a migration and a re-index.
EMBEDDING_DIMENSION = 1024
# One ChunkUpload carries at most 256 chunks (ADR-0018); a search returns at most 100.
MAX_UPSERT = 256
MAX_K = 100


def check_vector(vector: Sequence[float]) -> None:
    """1024 finite numbers, not all zero: a zero vector has no cosine distance."""
    if len(vector) != EMBEDDING_DIMENSION:
        raise InvalidValueError(f"a vector has {EMBEDDING_DIMENSION} dimensions")
    if not all(math.isfinite(x) for x in vector):
        raise InvalidValueError("a vector holds finite numbers only")
    if not any(vector):
        raise InvalidValueError("a vector must not be all zero")


def check_write(
    repository_id: UUID, records: Sequence["VectorRecord"], leaf_node_ids: Sequence[str] = ()
) -> None:
    """The limits every `VectorStore` write applies, in the adapter and the fake alike."""
    if len(records) > MAX_UPSERT or len(leaf_node_ids) > MAX_UPSERT:
        raise InvalidValueError(f"at most {MAX_UPSERT} chunks per call")
    if any(r.chunk.repository_id != repository_id for r in records):
        raise InvalidValueError("a record belongs to another repository")
    if not all(is_hmac_hex(leaf) for leaf in leaf_node_ids):
        raise InvalidValueError("a leaf node id is 64-char lowercase hex")


@dataclass(frozen=True, slots=True)
class VectorRecord:
    """One embedded chunk; `chunk.leaf_node_id` is its key within the repository."""

    chunk: ChunkRef
    vector: Sequence[float] = field(repr=False)
    model: ModelRef

    def __post_init__(self) -> None:
        check_vector(self.vector)


@dataclass(frozen=True, slots=True)
class SearchHit:
    """`score` is the cosine similarity, `1 - cosine distance`."""

    chunk: ChunkRef
    score: float
