"""The `index.vectors` consumer (ADR-0018 amendment, slice 8c2) with fakes: a result
settles its pending leaf once; a repeated delivery is acknowledged; a result for a leaf
another repository has, or for content a later sync replaced, writes nothing and is
dead-lettered `not_pending`; a vector of another model leaves its leaf pending; a store
outage is a retry. Nothing it emits holds a vector."""

import dataclasses
import uuid

import pytest
import structlog
from opentelemetry.sdk.trace import TracerProvider
from prometheus_client import CollectorRegistry
from support.contracts import merkle as contract
from support.contracts import vectors as vector_contract
from support.merkle import FakeMerkleIndex

from backend.messaging.vectors import OUTCOMES, ResultMetrics, VectorResults
from common_lib.errors import DependencyUnavailableError, InvalidValueError
from common_lib.index_messages import EmbeddedChunk, RejectedChunk, WireChunkRef, encode
from common_lib.merkle import LeafResult, MerkleUpdate, Settled
from common_lib.messaging import REASONS, Delivery


class World:
    def __init__(self, index: FakeMerkleIndex | None = None) -> None:
        self.index = index or FakeMerkleIndex()
        self.registry = CollectorRegistry()
        self.results = VectorResults(
            self.index,
            model=vector_contract.MODEL,
            metrics=ResultMetrics.create(self.registry),
            tracer=TracerProvider().get_tracer("test"),
        )
        self.repo, self.other = uuid.uuid4(), uuid.uuid4()

    async def synced(self) -> "World":
        for repository_id in (self.repo, self.other):
            self.index.register(repository_id)
            await self.index.apply(
                repository_id, None, MerkleUpdate(contract.ROOT_1, contract.TREE)
            )
        return self

    async def deliver(self, result: LeafResult, attempt: int = 0) -> str | None:
        chunk = WireChunkRef.of(result.chunk)
        if result.embedded is not None:
            message: EmbeddedChunk | RejectedChunk = EmbeddedChunk(
                chunk=chunk,
                model_id=result.embedded.model.model_id,
                model_version=result.embedded.model.model_version,
                vector=list(result.embedded.vector),
            )
        else:
            assert result.reason is not None
            message = RejectedChunk(chunk=chunk, reason=result.reason)
        return await self.results.handle(Delivery(encode(message), "m-1", "corr-1", attempt))

    def count(self, outcome: str) -> float:
        value = self.registry.get_sample_value("xype_index_results_total", {"outcome": outcome})
        return value or 0.0


async def test_a_result_settles_its_leaf_once_and_leaks_no_vector() -> None:
    world = await World().synced()
    first = contract.TREE[2]
    with structlog.testing.capture_logs() as logs:
        assert await world.deliver(contract.result(world.repo, first)) is None
        assert await world.deliver(contract.result(world.repo, first), attempt=1) is None
    stored = await world.index.vector(world.repo, first.node_id)
    assert stored is not None
    assert (stored.chunk.line_start, stored.chunk.line_end) == first.lines
    assert await world.index.state(world.repo, first.node_id) == ("indexed", None)
    assert (world.count("settled"), world.count("already_settled")) == (1, 1)
    assert "1.0" not in repr(logs)


async def test_a_rejection_settles_its_leaf_without_a_vector() -> None:
    world = await World().synced()
    second = contract.TREE[3]
    assert await world.deliver(contract.result(world.repo, second, reason="unscannable")) is None
    assert await world.index.state(world.repo, second.node_id) == ("rejected", "unscannable")
    assert await world.index.vector(world.repo, second.node_id) is None


async def test_a_result_for_another_repository_writes_nothing() -> None:
    """The cross-team case: the same leaf ids in a repository that never had them
    pending under this file, or a repository that does not exist."""
    world = World()
    world.index.register(world.repo)
    world.index.register(world.other)
    await world.index.apply(world.repo, None, MerkleUpdate(contract.ROOT_1, contract.TREE))
    forged = contract.result(world.other, contract.TREE[2])
    assert await world.deliver(forged) == "not_pending"
    assert await world.deliver(contract.result(uuid.uuid4(), contract.TREE[2])) == "not_pending"
    for repository_id in (world.repo, world.other):
        assert await world.index.vector(repository_id, contract.L1) is None
    assert await world.index.state(world.repo, contract.L1) == ("pending", None)


async def test_a_late_result_for_replaced_content_is_dead_lettered() -> None:
    world = await World().synced()
    edited = contract.leaf(contract.L1, contract.F, 9, (1, 10))
    await world.index.apply(world.repo, contract.ROOT_1, MerkleUpdate(contract.ROOT_2, (edited,)))
    assert await world.deliver(contract.result(world.repo, contract.TREE[2])) == "not_pending"
    assert await world.index.vector(world.repo, contract.L1) is None
    assert world.count("not_pending") == 1


async def test_a_message_that_is_not_a_result_is_malformed() -> None:
    world = await World().synced()
    for body in (b'{"not": "a result"}', b"\xff"):
        assert await world.results.handle(Delivery(body, "m-1", "corr-1", 0)) == "malformed"
    assert world.count("malformed") == 2


async def test_a_store_outage_is_a_retry() -> None:
    class Down(FakeMerkleIndex):
        async def settle(self, result: LeafResult) -> Settled:
            raise DependencyUnavailableError("database")

    world = World(Down())
    with pytest.raises(DependencyUnavailableError):
        await world.deliver(contract.result(world.repo, contract.TREE[2]))


async def test_a_value_the_store_refuses_is_malformed() -> None:
    class Refusing(FakeMerkleIndex):
        async def settle(self, result: LeafResult) -> Settled:
            raise InvalidValueError("a CHECK refused the row")

    world = World(Refusing())
    assert await world.deliver(contract.result(world.repo, contract.TREE[2])) == "malformed"
    assert world.count("malformed") == 1


async def test_a_vector_of_another_model_leaves_its_leaf_pending() -> None:
    """Stored, it would never be found by a search with the backend's model, and the
    leaf, settled, would never be embedded again (security review of slice 8c2)."""
    world = await World().synced()
    first = contract.TREE[2]
    good = contract.result(world.repo, first)
    assert good.embedded is not None
    other = dataclasses.replace(good.embedded.model, model_version="other")
    wrong = dataclasses.replace(good, embedded=dataclasses.replace(good.embedded, model=other))
    assert await world.deliver(wrong) == "wrong_model"
    assert await world.index.state(world.repo, first.node_id) == ("pending", None)
    assert await world.index.vector(world.repo, first.node_id) is None
    assert await world.deliver(good) is None  # the right model still settles it
    assert world.count("wrong_model") == 1


def test_every_dead_letter_outcome_is_a_known_reason() -> None:
    """Else the bus files it as `handler_error`, and the DLQ cannot tell them apart."""
    routine = {"settled", "already_settled"}
    assert set(OUTCOMES) - routine <= REASONS
