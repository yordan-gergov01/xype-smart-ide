"""The index messages (ADR-0015): they round-trip, refuse anything else with a fixed
message that never repeats the body, and stay within their size."""

import base64
import json
import os
import uuid

import pytest

from common_lib.domain import ChunkRef
from common_lib.errors import InvalidValueError
from common_lib.index_messages import (
    MAX_MESSAGE_BYTES,
    EmbeddedChunk,
    RejectedChunk,
    WireChunkRef,
    decode_job,
    decode_result,
    encode,
)
from common_lib.vectors import EMBEDDING_DIMENSION

HEX = "a" * 64
REF = ChunkRef(uuid.uuid4(), HEX, 3, 9, "b" * 64, "c" * 64)


def job(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "version": 1,
        "user_id": str(uuid.uuid4()),
        "team_id": str(uuid.uuid4()),
        "key_id": str(uuid.uuid4()),
        "message_id": "m-1",
        "request_id": "r-1",
        "field_number": 3,
        "chunk": json.loads(WireChunkRef.of(REF).model_dump_json()),
        "nonce": base64.b64encode(os.urandom(12)).decode(),
        "ciphertext": base64.b64encode(os.urandom(48)).decode(),
    }
    values.update(overrides)
    return values


def test_messages_round_trip() -> None:
    chunk_job = decode_job(json.dumps(job()).encode())
    assert decode_job(encode(chunk_job)) == chunk_job
    assert chunk_job.chunk.ref() == REF
    embedded = EmbeddedChunk(
        chunk=WireChunkRef.of(REF),
        model_id="qwen3-embedding",
        model_version="0.6b",
        vector=[0.5] * EMBEDDING_DIMENSION,
    )
    rejected = RejectedChunk(chunk=WireChunkRef.of(REF), reason="secret_found")
    for result in (embedded, rejected):
        assert decode_result(encode(result)) == result
    assert embedded.record().chunk == REF


@pytest.mark.parametrize(
    "overrides",
    [
        {"version": 2},
        {"extra": "src/app.py"},
        {"message_id": "has spaces"},
        {"field_number": 0},
        {"nonce": base64.b64encode(b"short").decode()},
        {"ciphertext": base64.b64encode(os.urandom(64 * 1024 + 17)).decode()},
        {
            "chunk": {
                **json.loads(WireChunkRef.of(REF).model_dump_json()),
                "obfuscated_file_id": "src/app.py",
            }
        },
        {"chunk": {**json.loads(WireChunkRef.of(REF).model_dump_json()), "line_start": 0}},
        {"user_id": "not-a-uuid"},
    ],
    ids=[
        "version",
        "extra field",
        "unsafe id",
        "field zero",
        "short nonce",
        "huge ciphertext",
        "plain path",
        "line zero",
        "bad uuid",
    ],
)
def test_a_job_refuses_anything_else_without_repeating_it(overrides: dict[str, object]) -> None:
    body = json.dumps(job(**overrides)).encode()
    with pytest.raises(InvalidValueError) as refused:
        decode_job(body)
    assert str(refused.value) in {"not an index.chunks message", "index message too large"}


@pytest.mark.parametrize(
    "body",
    [
        {"outcome": "embedded", "chunk": {}, "model_id": "m", "model_version": "v", "vector": []},
        {"outcome": "rejected", "reason": "found AKIA1234 in a file"},
        {"outcome": "deleted"},
        {"version": 1},
    ],
    ids=["short vector", "free-text reason", "unknown outcome", "no outcome"],
)
def test_a_result_refuses_anything_else(body: dict[str, object]) -> None:
    with pytest.raises(InvalidValueError) as refused:
        decode_result(json.dumps(body).encode())
    assert "AKIA" not in str(refused.value)


def test_oversized_bodies_are_refused_before_parsing() -> None:
    with pytest.raises(InvalidValueError):
        decode_job(b" " * (MAX_MESSAGE_BYTES + 1))
    with pytest.raises(InvalidValueError):
        decode_result(b" " * (MAX_MESSAGE_BYTES + 1))


def test_a_reversed_range_is_refused_when_it_becomes_a_ref() -> None:
    wire = WireChunkRef.of(REF).model_copy(update={"line_start": 20})
    with pytest.raises(InvalidValueError):
        wire.ref()
