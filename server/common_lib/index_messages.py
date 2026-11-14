"""Messages on the `xype.index` exchange (ADR-0015, ADR-0004): versioned JSON, shared by
the backend and the indexer. Protobuf stays in the gRPC layer (import contract).

No field can hold plaintext code, a path, a symbol or a query: there are ids, HMAC hex,
line numbers, model ids, vectors and AES-256-GCM ciphertext, nothing else. Every field
is on the reviewed allow-list in tests/unit/common_lib/test_message_privacy.py.
"""

import uuid
from typing import Annotated, Literal

from pydantic import (
    Base64Bytes,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
)

from common_lib.domain import ChunkRef, ModelRef
from common_lib.errors import InvalidValueError
from common_lib.vectors import EMBEDDING_DIMENSION, VectorRecord

SCHEMA_VERSION: Literal[1] = 1
# A chunk is at most 64 KiB of scrubbed text (ADR-0019) plus the 16-byte GCM tag.
MAX_CIPHERTEXT_BYTES = 64 * 1024 + 16
# Refused before parsing: base64 adds a third, the rest is ids.
MAX_MESSAGE_BYTES = 128 * 1024

type Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
type Line = Annotated[int, Field(ge=1, le=2**31 - 1)]
# Client-chosen ids (envelope message id, request id), as correlation ids are checked.
type SafeId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._-]{1,128}$")]
type ModelPart = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._:/-]{1,128}$")]


class _Message(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: Literal[1] = SCHEMA_VERSION


class WireChunkRef(_Message):
    """`ChunkRef` on the wire; `chunk_hash` and `leaf_node_id` bind the stored row."""

    repository_id: uuid.UUID
    obfuscated_file_id: Hex
    line_start: Line
    line_end: Line
    chunk_hash: Hex
    leaf_node_id: Hex

    @classmethod
    def of(cls, ref: ChunkRef) -> "WireChunkRef":
        return cls(
            repository_id=ref.repository_id,
            obfuscated_file_id=ref.obfuscated_file_id,
            line_start=ref.line_start,
            line_end=ref.line_end,
            chunk_hash=ref.chunk_hash,
            leaf_node_id=ref.leaf_node_id,
        )

    def ref(self) -> ChunkRef:
        """Raises `InvalidValueError` for a reversed line range."""
        return ChunkRef(
            self.repository_id,
            self.obfuscated_file_id,
            self.line_start,
            self.line_end,
            self.chunk_hash,
            self.leaf_node_id,
        )


class ChunkJob(_Message):
    """`index.chunks`: one encrypted chunk for the indexer. The plain fields are what the
    indexer needs to rebuild the AAD and find the key; the gateway wrote and checked
    them (ADR-0015), the client's AAD bytes are never forwarded."""

    user_id: uuid.UUID
    team_id: uuid.UUID
    key_id: uuid.UUID
    message_id: SafeId
    request_id: SafeId
    field_number: Annotated[int, Field(ge=1, le=2**29 - 1)]
    chunk: WireChunkRef
    nonce: Annotated[Base64Bytes, Field(min_length=12, max_length=12)]
    ciphertext: Annotated[Base64Bytes, Field(min_length=16, max_length=MAX_CIPHERTEXT_BYTES)]


class EmbeddedChunk(_Message):
    """`index.vectors`: a chunk's vector and the model that made it."""

    outcome: Literal["embedded"] = "embedded"
    chunk: WireChunkRef
    model_id: ModelPart
    model_version: ModelPart
    vector: Annotated[
        list[float], Field(min_length=EMBEDDING_DIMENSION, max_length=EMBEDDING_DIMENSION)
    ]

    def record(self) -> VectorRecord:
        """Raises `InvalidValueError` for a vector that cannot be stored."""
        return VectorRecord(
            self.chunk.ref(), self.vector, ModelRef(self.model_id, self.model_version)
        )


class RejectedChunk(_Message):
    """`index.vectors`: a chunk the indexer did not embed, with a fixed reason only."""

    outcome: Literal["rejected"] = "rejected"
    chunk: WireChunkRef
    reason: Literal["secret_found", "too_large"]


type IndexResult = EmbeddedChunk | RejectedChunk

_RESULT: TypeAdapter[IndexResult] = TypeAdapter(
    Annotated[EmbeddedChunk | RejectedChunk, Field(discriminator="outcome")]
)


def encode(message: ChunkJob | EmbeddedChunk | RejectedChunk) -> bytes:
    return message.model_dump_json().encode("utf-8")


def decode_job(body: bytes) -> ChunkJob:
    """Raises `InvalidValueError` (a fixed message, never the body) for anything else."""
    _check_size(body)
    try:
        return ChunkJob.model_validate_json(body)
    except ValidationError:
        raise InvalidValueError("not an index.chunks message") from None


def decode_result(body: bytes) -> IndexResult:
    _check_size(body)
    try:
        return _RESULT.validate_json(body, strict=True)
    except ValidationError:
        raise InvalidValueError("not an index.vectors message") from None


def _check_size(body: bytes) -> None:
    if len(body) > MAX_MESSAGE_BYTES:
        raise InvalidValueError("index message too large")
