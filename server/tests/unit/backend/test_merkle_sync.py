"""The server side of the Merkle sync (ADR-0018) against the in-memory index, with a
client tree that hashes like the extension. Covers the ADR's confirmation list: equal
trees, one changed chunk, a deleted file, moved lines, a dropped page and the number of
round trips, plus the refusals that keep a broken report from deleting anything."""

import dataclasses
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from support.clock import FakeClock
from support.merkle import FakeMerkleIndex
from support.merkle_client import ClientTree, Spec, drive

from backend.services.indexing import (
    DELTA_PAGE,
    SYNC_MALFORMED,
    SYNC_MISMATCH,
    SYNC_TOO_LARGE,
    SYNC_UNAVAILABLE,
    SYNC_UNKNOWN,
    MerkleSyncs,
    NodeBudget,
    SyncDelta,
    SyncDone,
    SyncPolicy,
    SyncRequest,
    TreeReport,
)
from backend.services.session import NO_ACCESS, RefusedError
from common_lib.errors import DependencyUnavailableError
from common_lib.merkle import MerkleNodeUpdate, MerkleUpdate


def h(n: int) -> str:
    return f"{n:064x}"


# dir D -> file F -> leaves L1, L2; file G -> leaf L3.
D, F, G, L1, L2, L3 = (h(n) for n in (0xD, 0xF, 0x6, 0x11, 0x12, 0x13))
F_LEAVES = [(L1, h(0xC1), (1, 10)), (L2, h(0xC2), (12, 20))]
BASE: Spec = {
    D: {F: F_LEAVES},
    G: [(L3, h(0xC3), (1, 5))],
}
POLICY = SyncPolicy(deadline_s=300, max_restarts=1)


class World:
    def __init__(
        self, spec: Spec | None = None, policy: SyncPolicy = POLICY, budget: int = 10**6
    ) -> None:
        self.repo = uuid.uuid4()
        self.index = FakeMerkleIndex()
        self.index.register(self.repo)
        self.clock = FakeClock(datetime(2026, 11, 13, 9, 0, tzinfo=UTC))
        self.budget = NodeBudget(budget)
        self.syncs = MerkleSyncs(
            self.index, frozenset({self.repo}), policy=policy, budget=self.budget, clock=self.clock
        )
        self.server = ClientTree(spec or BASE)

    async def settle(self) -> None:
        """The server holds BASE, as after an initial index whose uploads all settled."""
        await self.index.apply(self.repo, None, self.server.as_update())

    def stored(self) -> dict[str, MerkleNodeUpdate]:
        return self.index.nodes[self.repo]


async def test_equal_trees_need_one_message() -> None:
    world = World()
    await world.settle()
    assert await world.syncs.start(world.repo, world.server.root) == []
    # The poll asks for the root's children and stops there when the roots are equal.
    steps, requests = await drive(world.syncs, world.server, await world.syncs.poll())
    assert (steps, requests) == ([], 1)


async def test_one_changed_chunk_gives_one_upload_after_depth_round_trips() -> None:
    world = World()
    await world.settle()
    changed = ClientTree({D: {F: [(L1, h(0xC9), (1, 10)), (L2, h(0xC2), (12, 20))]}, G: BASE[G]})
    steps, requests = await drive(
        world.syncs, changed, await world.syncs.start(world.repo, changed.root)
    )
    assert requests == 3  # root, D, F: the depth of the changed leaf
    [delta] = steps
    assert isinstance(delta, SyncDelta)
    assert [(c.leaf_node_id, c.obfuscated_file_id, c.chunk_hash) for c in delta.upload] == [
        (L1, F, h(0xC9))
    ]
    assert delta.deleted == ()
    assert world.index.roots[world.repo] == world.server.root  # held until uploads settle


async def test_a_deleted_file_takes_its_subtree_without_uploads() -> None:
    world = World()
    await world.settle()
    without_g = ClientTree({D: BASE[D]})
    steps, _ = await drive(
        world.syncs, without_g, await world.syncs.start(world.repo, without_g.root)
    )
    assert [type(s) for s in steps] == [SyncDone]
    assert world.index.roots[world.repo] == without_g.root
    assert set(world.stored()) == {D, F, L1, L2}


async def test_moved_lines_update_the_range_and_upload_nothing() -> None:
    world = World()
    await world.settle()
    moved = ClientTree({D: {F: [(L1, h(0xC1), (4, 13)), (L2, h(0xC2), (15, 23))]}, G: BASE[G]})
    steps, _ = await drive(world.syncs, moved, await world.syncs.start(world.repo, moved.root))
    assert [type(s) for s in steps] == [SyncDone]
    assert world.stored()[L1].lines == (4, 13)
    assert world.index.roots[world.repo] == moved.root


async def test_a_dropped_page_deletes_nothing() -> None:
    world = World()
    await world.settle()
    without_g = ClientTree({D: BASE[D]})
    [request] = await world.syncs.start(world.repo, without_g.root)
    assert isinstance(request, SyncRequest)
    first, _second = ClientTree(BASE).answer(request, page=1)  # D's page arrives, G's does not
    with pytest.raises(RefusedError) as refused:
        await world.syncs.report(dataclasses.replace(first, last_page=True))
    assert refused.value.reason == SYNC_MISMATCH
    assert world.index.roots[world.repo] == world.server.root
    assert G in world.stored()
    with pytest.raises(RefusedError):  # the sync is forgotten
        await world.syncs.report(first)


async def test_a_tree_that_changes_mid_sync_is_refused() -> None:
    world = World()
    await world.settle()
    changed = ClientTree({D: {F: [(L1, h(0xC9), (1, 10)), (L2, h(0xC2), (12, 20))]}, G: BASE[G]})
    [first] = await world.syncs.start(world.repo, changed.root)
    assert isinstance(first, SyncRequest)
    [request] = await world.syncs.report(changed.answer(first)[0])  # D next
    assert isinstance(request, SyncRequest)
    later = ClientTree({D: {F: [(L1, h(0xCA), (1, 10)), (L2, h(0xC2), (12, 20))]}, G: BASE[G]})
    [report] = later.answer(request)
    with pytest.raises(RefusedError) as refused:
        await world.syncs.report(report)
    assert refused.value.reason == SYNC_MISMATCH
    assert world.index.roots[world.repo] == world.server.root


@pytest.mark.parametrize(
    "nodes",
    [
        (MerkleNodeUpdate(h(0x99), None, h(1), True, (1, 2)),),  # a leaf at the top level
        (MerkleNodeUpdate(h(0x99), h(0x77), h(1), False),),  # a parent nobody asked for
        (
            MerkleNodeUpdate(D, None, h(1), False),
            MerkleNodeUpdate(D, None, h(1), False),
        ),  # the same node twice
    ],
    ids=["top-level leaf", "unrequested parent", "repeated node"],
)
async def test_malformed_reports_are_refused(nodes: tuple[MerkleNodeUpdate, ...]) -> None:
    world = World()
    [request] = await world.syncs.start(world.repo, h(0xAB))
    assert isinstance(request, SyncRequest)
    report = TreeReport(request.request_id, world.repo, h(0xAB), nodes, last_page=False)
    with pytest.raises(RefusedError) as refused:
        await world.syncs.report(report)
    assert refused.value.reason == SYNC_MALFORMED


async def test_unknown_stale_expired_and_foreign_syncs_are_refused() -> None:
    world = World()
    with pytest.raises(RefusedError) as refused:
        await world.syncs.start(uuid.uuid4(), h(1))
    assert refused.value.reason == NO_ACCESS
    [request] = await world.syncs.start(world.repo, h(0xAB))
    assert isinstance(request, SyncRequest)
    stale = TreeReport("not-the-request", world.repo, h(0xAB), (), last_page=True)
    world.clock.advance(timedelta(seconds=POLICY.deadline_s))
    for report in (stale, TreeReport(request.request_id, world.repo, h(0xAB), (), True)):
        with pytest.raises(RefusedError) as refused:
            await world.syncs.report(report)
        assert refused.value.reason == SYNC_UNKNOWN


async def test_a_conflict_restarts_from_the_root_a_bounded_number_of_times() -> None:
    world = World()  # max_restarts=1
    await world.settle()
    without_g = ClientTree({D: BASE[D]})
    [request] = await world.syncs.start(world.repo, without_g.root)
    assert isinstance(request, SyncRequest)
    # Another stream applies first, twice: the stored root moves under this sync.
    await world.index.apply(world.repo, world.server.root, MerkleUpdate(h(0xEE), ()))
    [restart] = await world.syncs.report(without_g.answer(request)[0])
    assert isinstance(restart, SyncRequest)
    await world.index.apply(world.repo, h(0xEE), MerkleUpdate(h(0xEF), ()))
    assert await world.syncs.report(without_g.answer(restart)[0]) == []  # it gives up
    assert world.index.roots[world.repo] == h(0xEF)
    assert G in world.stored()


async def test_large_deltas_are_paged_and_a_stream_holds_a_bounded_tree() -> None:
    leaves = [(h(0x1000 + n), h(0x2000 + n), (n + 1, n + 1)) for n in range(DELTA_PAGE + 1)]
    world = World()
    big = ClientTree({D: {F: leaves}})
    steps, _ = await drive(world.syncs, big, await world.syncs.start(world.repo, big.root))
    assert [len(s.upload) for s in steps if isinstance(s, SyncDelta)] == [DELTA_PAGE, 1]
    small = World(policy=SyncPolicy(deadline_s=300, max_restarts=1, max_nodes_per_stream=10))
    with pytest.raises(RefusedError) as refused:
        await drive(small.syncs, big, await small.syncs.start(small.repo, big.root))
    assert refused.value.reason == SYNC_TOO_LARGE


@pytest.mark.parametrize(
    "client",
    [
        {D: BASE[D], G: {L3: [(h(0x77), h(0xC7), (1, 5))]}},  # leaf L3 comes back as a file
        {D: {F: F_LEAVES, G: BASE[G]}},  # file G moves below D: stored and deleted
    ],
    ids=["changes kind", "moves parent"],
)
async def test_a_node_that_changes_kind_or_moves_parent_is_refused(client: Spec) -> None:
    world = World()
    await world.settle()
    tree = ClientTree(client)
    with pytest.raises(RefusedError) as refused:
        await drive(world.syncs, tree, await world.syncs.start(world.repo, tree.root))
    assert refused.value.reason == SYNC_MALFORMED
    assert world.index.roots[world.repo] == world.server.root
    assert world.budget.used == 0


async def test_a_failing_apply_refuses_the_sync_and_frees_its_nodes() -> None:
    world = World()
    await world.settle()
    without_g = ClientTree({D: BASE[D]})

    async def unavailable(*_: object) -> None:
        raise DependencyUnavailableError("database")

    world.index.apply = unavailable  # type: ignore[method-assign,assignment]
    with pytest.raises(RefusedError) as refused:
        await drive(world.syncs, without_g, await world.syncs.start(world.repo, without_g.root))
    assert refused.value.reason == SYNC_UNAVAILABLE
    assert world.budget.used == 0


async def test_streams_share_one_budget_and_give_it_back() -> None:
    first, second = World(budget=6), World()
    second.syncs = MerkleSyncs(
        second.index,
        frozenset({second.repo}),
        policy=POLICY,
        budget=first.budget,
        clock=second.clock,
    )
    tree = ClientTree(BASE)
    [request] = await first.syncs.start(first.repo, tree.root)
    assert isinstance(request, SyncRequest)
    for report in tree.answer(request):  # the top level: 2 of the 6 nodes
        await first.syncs.report(report)
    big = ClientTree({D: {F: [(h(0x100 + n), h(0x200 + n), (n + 1, n + 1)) for n in range(5)]}})
    with pytest.raises(RefusedError) as refused:
        await drive(second.syncs, big, await second.syncs.start(second.repo, big.root))
    assert refused.value.reason == SYNC_TOO_LARGE
    assert first.budget.used == 2
    first.syncs.close()
    assert first.budget.used == 0
