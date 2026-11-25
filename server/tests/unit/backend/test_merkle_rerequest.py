"""The re-request of stale pending leaves (ADR-0018 amendment, slice 8c3c): every sync
that ends asks again for the leaves the indexer never settled, after the settle window,
a bounded number of times; a store outage only postpones them."""

import uuid
from datetime import UTC, datetime, timedelta

from support.clock import FakeClock
from support.contracts.merkle import result
from support.merkle import FakeMerkleIndex
from support.merkle_client import ClientTree, Spec, drive

from backend.services.indexing import MerkleSyncs, NodeBudget, SyncDelta, SyncPolicy
from common_lib.errors import DependencyUnavailableError
from common_lib.merkle import MerkleNodeUpdate


def h(n: int) -> str:
    return f"{n:064x}"


D, F, G, L1, L2, L3 = (h(n) for n in (0xD, 0xF, 0x6, 0x11, 0x12, 0x13))
BASE: Spec = {D: {F: [(L1, h(0xC1), (1, 10)), (L2, h(0xC2), (12, 20))]}, G: [(L3, h(0xC3), (1, 5))]}
WINDOW = timedelta(seconds=900)
POLICY = SyncPolicy(deadline_s=300, max_restarts=1, max_request_attempts=2)


class World:
    def __init__(self) -> None:
        self.repo = uuid.uuid4()
        self.clock = FakeClock(datetime(2026, 11, 24, 9, 0, tzinfo=UTC))
        self.index = FakeMerkleIndex(self.clock)
        self.index.register(self.repo)
        self.syncs = MerkleSyncs(
            self.index,
            frozenset({self.repo}),
            policy=POLICY,
            budget=NodeBudget(10**6),
            clock=self.clock,
        )
        self.tree = ClientTree(BASE)

    async def ready(self) -> "World":
        """BASE stored, nothing settled yet."""
        await self.index.apply(self.repo, None, self.tree.as_update())
        return self

    def leaf(self, leaf_id: str) -> MerkleNodeUpdate:
        return next(n for n in self.tree.nodes if n.node_id == leaf_id)

    async def asked(self) -> list[str]:
        steps = await self.syncs.start(self.repo, self.tree.root)
        return [
            ref.leaf_node_id for step in steps if isinstance(step, SyncDelta) for ref in step.upload
        ]


async def test_stale_pending_leaves_are_asked_for_again_on_equal_roots() -> None:
    world = await World().ready()
    assert await world.asked() == []  # within the window: the indexer may still be on it
    world.clock.advance(WINDOW + timedelta(seconds=1))
    assert await world.asked() == [L1, L2, L3]
    assert await world.asked() == []  # asked for just now
    assert world.index.asks[world.repo][L1].attempts == 1


async def test_a_settled_or_recently_published_leaf_is_not_asked_for() -> None:
    world = await World().ready()
    assert await world.index.settle(result(world.repo, world.leaf(L1))) == "settled"
    world.clock.advance(WINDOW)
    await world.index.mark_published(world.repo, [L3])  # the indexer has it
    world.clock.advance(timedelta(seconds=1))
    assert await world.asked() == [L2]


async def test_a_leaf_is_asked_for_a_bounded_number_of_times_and_stays_pending() -> None:
    world = await World().ready()
    for expected in ([L1, L2, L3], [L1, L2, L3], []):
        world.clock.advance(WINDOW + timedelta(seconds=1))
        assert await world.asked() == expected
    assert await world.index.state(world.repo, L1) == ("pending", None)


async def test_a_sync_delta_carries_the_changes_and_the_stale_leaves() -> None:
    world = await World().ready()
    world.clock.advance(WINDOW + timedelta(seconds=1))
    changed = ClientTree(
        {D: {F: [(L1, h(0xC9), (1, 10)), (L2, h(0xC2), (12, 20))]}, G: [(L3, h(0xC3), (1, 5))]}
    )
    steps, _ = await drive(world.syncs, changed, await world.syncs.start(world.repo, changed.root))
    [delta] = steps
    assert isinstance(delta, SyncDelta)
    assert sorted((r.leaf_node_id, r.chunk_hash) for r in delta.upload) == [
        (L1, h(0xC9)),  # changed: pending anew
        (L2, h(0xC2)),  # stale
        (L3, h(0xC3)),  # stale
    ]


async def test_a_store_outage_postpones_the_rerequest() -> None:
    world = await World().ready()
    world.clock.advance(WINDOW + timedelta(seconds=1))

    async def unavailable(*_: object, **__: object) -> None:
        raise DependencyUnavailableError("database")

    world.index.rerequest = unavailable  # type: ignore[method-assign,assignment]
    assert await world.asked() == []
    del world.index.rerequest
    assert await world.asked() == [L1, L2, L3]
