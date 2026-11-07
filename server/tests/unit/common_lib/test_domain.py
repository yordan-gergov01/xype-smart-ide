from uuid import uuid4

import pytest

from common_lib.domain import ChunkRef, ModelRef, RequestContext
from common_lib.errors import XypeError

HEX = "a" * 64


def chunk(**overrides: object) -> ChunkRef:
    values: dict[str, object] = {
        "repository_id": uuid4(),
        "obfuscated_file_id": HEX,
        "line_start": 1,
        "line_end": 10,
        "chunk_hash": "b" * 64,
    }
    values.update(overrides)
    return ChunkRef(**values)  # type: ignore[arg-type]


def test_valid_chunk_ref_keeps_its_values() -> None:
    ref = chunk(line_start=3, line_end=3)
    assert (ref.line_start, ref.line_end) == (3, 3)


@pytest.mark.parametrize(
    "overrides",
    [
        {"line_start": 0},
        {"line_start": 5, "line_end": 4},
        {"obfuscated_file_id": "src/app.py"},
        {"chunk_hash": "A" * 64},
        {"chunk_hash": "a" * 63},
    ],
)
def test_chunk_ref_rejects_invalid_values(overrides: dict[str, object]) -> None:
    with pytest.raises(XypeError):
        chunk(**overrides)


def test_model_ref_requires_id_and_version() -> None:
    assert ModelRef("mock", "1").model_version == "1"
    with pytest.raises(XypeError):
        ModelRef("mock", "")


def test_request_context_requires_correlation_id() -> None:
    ctx = RequestContext(uuid4(), uuid4(), None, "corr-1", "chat")
    assert ctx.task_type == "chat"
    with pytest.raises(XypeError):
        RequestContext(uuid4(), uuid4(), None, "", "chat")


def test_values_are_immutable() -> None:
    ref = ModelRef("mock", "1")
    with pytest.raises(AttributeError):
        ref.model_id = "other"  # type: ignore[misc]
