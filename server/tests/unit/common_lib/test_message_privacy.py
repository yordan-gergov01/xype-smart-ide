"""No queue message may carry plaintext code, a path, a symbol or a query (rule 07).

Every field of every index message is on a reviewed allow-list with the reason it is
safe; a new field fails this test until it is reviewed and added. Every string field
has a pattern, so free text cannot slip into one."""

import typing

from pydantic import BaseModel

from common_lib.index_messages import ChunkJob, EmbeddedChunk, RejectedChunk, WireChunkRef

REVIEWED_FIELDS = {
    "WireChunkRef.version": "schema version",
    "WireChunkRef.repository_id": "id",
    "WireChunkRef.obfuscated_file_id": "HMAC hex (ADR-0016)",
    "WireChunkRef.line_start": "line number",
    "WireChunkRef.line_end": "line number",
    "WireChunkRef.chunk_hash": "HMAC hex of the scrubbed content",
    "WireChunkRef.leaf_node_id": "HMAC hex of the position",
    "ChunkJob.version": "schema version",
    "ChunkJob.user_id": "id",
    "ChunkJob.team_id": "id",
    "ChunkJob.key_id": "key id, not key material",
    "ChunkJob.message_id": "client id, pattern-checked",
    "ChunkJob.request_id": "client id, pattern-checked",
    "ChunkJob.field_number": "protobuf field number",
    "ChunkJob.chunk": "the WireChunkRef above",
    "ChunkJob.nonce": "AES-GCM nonce",
    "ChunkJob.ciphertext": "AES-256-GCM ciphertext and tag (ADR-0015)",
    "EmbeddedChunk.version": "schema version",
    "EmbeddedChunk.outcome": "fixed literal",
    "EmbeddedChunk.chunk": "the WireChunkRef above",
    "EmbeddedChunk.model_id": "model id, pattern-checked",
    "EmbeddedChunk.model_version": "version string, pattern-checked",
    "EmbeddedChunk.vector": "embedding (model-inversion caveat: docs/privacy.md)",
    "RejectedChunk.version": "schema version",
    "RejectedChunk.outcome": "fixed literal",
    "RejectedChunk.chunk": "the WireChunkRef above",
    "RejectedChunk.reason": "fixed literal, never what was found",
}
MODELS: tuple[type[BaseModel], ...] = (WireChunkRef, ChunkJob, EmbeddedChunk, RejectedChunk)


def _fields() -> dict[str, typing.Any]:  # reason: Any - pydantic FieldInfo of any type
    return {f"{m.__name__}.{name}": info for m in MODELS for name, info in m.model_fields.items()}


def test_every_message_field_is_reviewed() -> None:
    fields = set(_fields())
    assert fields - REVIEWED_FIELDS.keys() == set(), "review and allow-list new fields"
    assert REVIEWED_FIELDS.keys() - fields == set(), "remove stale allow-list entries"


def test_every_string_field_has_a_pattern_or_is_a_literal() -> None:
    for name, info in _fields().items():
        annotation = info.annotation
        if annotation is str or typing.get_origin(annotation) is typing.Annotated:
            patterns = [getattr(m, "pattern", None) for m in info.metadata]
            assert any(patterns), name
        if isinstance(annotation, typing.TypeAliasType):
            value = annotation.__value__
            if typing.get_args(value) and typing.get_args(value)[0] is str:
                assert any(getattr(m, "pattern", None) for m in typing.get_args(value)[1:]), name
