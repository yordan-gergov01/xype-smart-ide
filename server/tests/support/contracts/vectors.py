"""Contract of `VectorStore` (ADR-0020): the fake (unit) and the pgvector adapter
(integration) both run it. `repositories` are two registered repository ids."""

import uuid
from collections.abc import Awaitable, Callable

import pytest

from backend.services.retrieval import VectorStore
from common_lib.domain import ChunkRef, ModelRef
from common_lib.errors import InvalidValueError
from common_lib.vectors import EMBEDDING_DIMENSION, MAX_UPSERT, VectorRecord

MODEL = ModelRef("qwen3-embedding", "0.6b")
OTHER_MODEL = ModelRef("qwen3-embedding", "0.6b-q8")
SCORE_TOLERANCE = 1e-4


def axis(*weights: tuple[int, float]) -> list[float]:
    """A vector with the given weights on the given axes, zero elsewhere."""
    vector = [0.0] * EMBEDDING_DIMENSION
    for index, weight in weights:
        vector[index] = weight
    return vector


def hexid(n: int) -> str:
    return f"{n:064x}"


def record(
    repository_id: uuid.UUID,
    leaf: int,
    vector: list[float],
    *,
    model: ModelRef = MODEL,
    lines: tuple[int, int] = (1, 5),
) -> VectorRecord:
    chunk = ChunkRef(
        repository_id=repository_id,
        obfuscated_file_id=hexid(0xF),
        line_start=lines[0],
        line_end=lines[1],
        chunk_hash=hexid(0xC),
        leaf_node_id=hexid(leaf),
    )
    return VectorRecord(chunk, vector, model)


type Check = Callable[[VectorStore, tuple[uuid.UUID, uuid.UUID]], Awaitable[None]]


async def check_upsert_is_keyed_by_leaf(
    store: VectorStore, repositories: tuple[uuid.UUID, uuid.UUID]
) -> None:
    repo, _ = repositories
    first = record(repo, 1, axis((0, 1.0)))
    twin = record(repo, 2, axis((0, 1.0)))  # identical content, another leaf
    await store.upsert(repo, [first, twin])
    await store.upsert(repo, [first])  # a repeated delivery
    await store.upsert(repo, [])
    # The same leaf twice in one call: the later record wins.
    await store.upsert(repo, [record(repo, 3, axis((5, 1.0))), record(repo, 3, axis((0, 1.0)))])
    moved = record(repo, 1, axis((0, 1.0)), lines=(10, 14))
    await store.upsert(repo, [moved])
    hits = await store.search(axis((0, 1.0)), repository_ids=[repo], model=MODEL, k=10)
    by_leaf = {h.chunk.leaf_node_id: h for h in hits}
    assert set(by_leaf) == {hexid(1), hexid(2), hexid(3)}
    assert abs(by_leaf[hexid(3)].score - 1.0) < SCORE_TOLERANCE
    assert by_leaf[hexid(1)].chunk == moved.chunk
    assert abs(by_leaf[hexid(1)].score - 1.0) < SCORE_TOLERANCE


async def check_delete_removes_only_those_leaves(
    store: VectorStore, repositories: tuple[uuid.UUID, uuid.UUID]
) -> None:
    repo, other = repositories
    await store.upsert(repo, [record(repo, 1, axis((1, 1.0))), record(repo, 2, axis((1, 1.0)))])
    await store.upsert(other, [record(other, 1, axis((1, 1.0)))])
    await store.delete(repo, [hexid(1), hexid(99)])
    await store.delete(repo, [])
    hits = await store.search(axis((1, 1.0)), repository_ids=[repo, other], model=MODEL, k=10)
    assert {(h.chunk.repository_id, h.chunk.leaf_node_id) for h in hits} == {
        (repo, hexid(2)),
        (other, hexid(1)),
    }


async def check_search_stays_in_scope_and_order(
    store: VectorStore, repositories: tuple[uuid.UUID, uuid.UUID]
) -> None:
    repo, other = repositories
    query = axis((2, 1.0))
    await store.upsert(
        repo,
        [
            record(repo, 1, axis((2, 1.0))),
            record(repo, 2, axis((2, 1.0), (3, 1.0))),
            record(repo, 3, axis((3, 1.0))),
            record(repo, 4, axis((2, 1.0)), model=OTHER_MODEL),
        ],
    )
    await store.upsert(other, [record(other, 1, axis((2, 1.0)))])
    hits = await store.search(query, repository_ids=[repo], model=MODEL, k=10)
    assert [h.chunk.leaf_node_id for h in hits] == [hexid(1), hexid(2), hexid(3)]
    assert [round(h.score, 3) for h in hits] == [1.0, 0.707, 0.0]
    top = await store.search(query, repository_ids=[repo], model=MODEL, k=1)
    assert [h.chunk.leaf_node_id for h in top] == [hexid(1)]
    close = await store.search(query, repository_ids=[repo], model=MODEL, k=10, min_score=0.5)
    assert len(close) == 2
    assert await store.search(query, repository_ids=[], model=MODEL, k=10) == ()


async def check_bad_input_is_refused(
    store: VectorStore, repositories: tuple[uuid.UUID, uuid.UUID]
) -> None:
    repo, _ = repositories
    for vector in ([1.0] * 3, axis(), axis((0, float("nan")))):
        with pytest.raises(InvalidValueError):
            await store.search(vector, repository_ids=[repo], model=MODEL, k=1)
    for k in (0, 101):
        with pytest.raises(InvalidValueError):
            await store.search(axis((0, 1.0)), repository_ids=[repo], model=MODEL, k=k)
    many = [record(repo, n, axis((0, 1.0))) for n in range(MAX_UPSERT + 1)]
    with pytest.raises(InvalidValueError):
        await store.upsert(repo, many)
    # A record naming another repository is refused, and nothing of the call is written.
    repo_b = repositories[1]
    with pytest.raises(InvalidValueError):
        await store.upsert(
            repo, [record(repo, 1, axis((0, 1.0))), record(repo_b, 2, axis((0, 1.0)))]
        )
    for leaves in ([hexid(n) for n in range(MAX_UPSERT + 1)], ["src/app.py#0"]):
        with pytest.raises(InvalidValueError):
            await store.delete(repo, leaves)
    assert await store.search(axis((0, 1.0)), repository_ids=[repo_b], model=MODEL, k=1) == ()
    assert await store.search(axis((0, 1.0)), repository_ids=[repo], model=MODEL, k=1) == ()


CHECKS: tuple[Check, ...] = (
    check_upsert_is_keyed_by_leaf,
    check_delete_removes_only_those_leaves,
    check_search_stays_in_scope_and_order,
    check_bad_input_is_refused,
)
