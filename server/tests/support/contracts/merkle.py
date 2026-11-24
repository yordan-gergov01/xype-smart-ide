"""Contract of `MerkleIndex` (ADR-0018): the fake (unit) and the Postgres adapter
(integration) both run it. `repositories` are two registered repositories that were
never synced (root None)."""

import uuid
from collections.abc import Awaitable, Callable

import pytest

from backend.services.indexing import MerkleIndex
from common_lib.domain import ChunkRef
from common_lib.errors import InvalidValueError
from common_lib.merkle import (
    MAX_CHUNKS_PER_UPLOAD,
    MAX_NODES_PER_CALL,
    LeafResult,
    MerkleConflictError,
    MerkleNodeUpdate,
    MerkleNodeView,
    MerkleUpdate,
    RejectionReason,
)
from common_lib.vectors import VectorRecord
from support.contracts import vectors as vector_contract


def hexid(n: int) -> str:
    return f"{n:064x}"


# Root -> dir D -> file F -> leaves L1, L2; root -> file G -> leaf L3.
ROOT_1, ROOT_2, ROOT_3 = hexid(0xA1), hexid(0xA2), hexid(0xA3)
D, F, G, L1, L2, L3 = (hexid(n) for n in (0xD, 0xF, 0x6, 0x11, 0x12, 0x13))


def inner(node_id: str, parent: str | None, hash_: int) -> MerkleNodeUpdate:
    return MerkleNodeUpdate(node_id, parent, hexid(hash_), is_leaf=False)


def leaf(node_id: str, parent: str, hash_: int, lines: tuple[int, int]) -> MerkleNodeUpdate:
    return MerkleNodeUpdate(node_id, parent, hexid(hash_), is_leaf=True, lines=lines)


TREE = (
    inner(D, None, 1),
    inner(F, D, 2),
    leaf(L1, F, 3, (1, 10)),
    leaf(L2, F, 4, (12, 20)),
    inner(G, None, 5),
    leaf(L3, G, 6, (1, 5)),
)


def view(node: MerkleNodeUpdate) -> MerkleNodeView:
    return MerkleNodeView(node.node_id, node.hash, node.is_leaf)


type Check = Callable[[MerkleIndex, tuple[uuid.UUID, uuid.UUID]], Awaitable[None]]


async def check_a_tree_is_stored_and_read_by_level(
    index: MerkleIndex, repositories: tuple[uuid.UUID, uuid.UUID]
) -> None:
    repo, other = repositories
    assert await index.root(repo) is None
    assert await index.children(repo, {None}) == {None: ()}
    assert await index.children(repo, set()) == {}
    await index.apply(repo, None, MerkleUpdate(ROOT_1, TREE))
    assert await index.root(repo) == ROOT_1
    tree = await index.children(repo, [None, D, F, L1, hexid(0xFFFF)])
    assert tree == {
        None: (view(TREE[4]), view(TREE[0])),  # sorted by node id: G (0x6) before D (0xD)
        D: (view(TREE[1]),),
        F: (view(TREE[2]), view(TREE[3])),
        L1: (),
        hexid(0xFFFF): (),
    }
    assert await index.root(other) is None
    assert await index.children(other, {None}) == {None: ()}


async def check_a_stale_root_changes_nothing(
    index: MerkleIndex, repositories: tuple[uuid.UUID, uuid.UUID]
) -> None:
    repo, _ = repositories
    await index.apply(repo, None, MerkleUpdate(ROOT_1, TREE))
    for stale in (None, ROOT_2):
        with pytest.raises(MerkleConflictError):
            await index.apply(repo, stale, MerkleUpdate(ROOT_3, (), deleted=(D,)))
    assert await index.root(repo) == ROOT_1
    assert (await index.children(repo, {None}))[None] == (view(TREE[4]), view(TREE[0]))


async def check_a_deleted_subtree_goes_whole(
    index: MerkleIndex, repositories: tuple[uuid.UUID, uuid.UUID]
) -> None:
    repo, _ = repositories
    await index.apply(repo, None, MerkleUpdate(ROOT_1, TREE))
    orphans = (
        (TREE[2],),  # L1 lies under D
        (inner(hexid(0x99), F, 9),),  # its parent F lies under D
    )
    for nodes in orphans:
        with pytest.raises(InvalidValueError):
            await index.apply(repo, ROOT_1, MerkleUpdate(ROOT_2, nodes, deleted=(D,)))
    assert await index.root(repo) == ROOT_1
    changed = inner(G, None, 7)
    await index.apply(repo, ROOT_1, MerkleUpdate(ROOT_2, (changed,), deleted=(D, hexid(0xEE))))
    tree = await index.children(repo, [None, D, F])
    assert tree == {None: (view(changed),), D: (), F: ()}
    assert (await index.children(repo, [G]))[G] == (view(TREE[5]),)


async def check_a_node_is_replaced_and_limits_hold(
    index: MerkleIndex, repositories: tuple[uuid.UUID, uuid.UUID]
) -> None:
    repo, _ = repositories
    await index.apply(repo, None, MerkleUpdate(ROOT_1, TREE))
    first, last = leaf(L3, G, 8, (2, 6)), leaf(L3, G, 9, (3, 7))
    await index.apply(repo, ROOT_1, MerkleUpdate(ROOT_2, (first, last)))  # the later wins
    assert (await index.children(repo, [G]))[G] == (view(last),)
    with pytest.raises(InvalidValueError):
        await index.children(repo, [hexid(n) for n in range(MAX_NODES_PER_CALL + 1)])


async def check_every_node_stays_reachable(
    index: MerkleIndex, repositories: tuple[uuid.UUID, uuid.UUID]
) -> None:
    repo, _ = repositories
    await index.apply(repo, None, MerkleUpdate(ROOT_1, TREE))
    a, b = hexid(0xA), hexid(0xB)
    refused = (
        (inner(a, hexid(0x77), 1),),  # a parent that does not exist
        (inner(a, b, 1), inner(b, a, 2)),  # a cycle
        (inner(D, L1, 1),),  # D moved below its own descendant
    )
    for nodes in refused:
        with pytest.raises(InvalidValueError):
            await index.apply(repo, ROOT_1, MerkleUpdate(ROOT_2, nodes))
    assert await index.root(repo) == ROOT_1
    assert (await index.children(repo, [None]))[None] == (view(TREE[4]), view(TREE[0]))
    with pytest.raises(InvalidValueError):
        await index.apply(uuid.uuid4(), None, MerkleUpdate(ROOT_1, TREE))  # unknown repository
    with pytest.raises(InvalidValueError):
        await index.children(repo, ["src/app.py"])


async def check_repositories_with_the_same_ids_stay_apart(
    index: MerkleIndex, repositories: tuple[uuid.UUID, uuid.UUID]
) -> None:
    repo, other = repositories
    for repository_id in repositories:
        await index.apply(repository_id, None, MerkleUpdate(ROOT_1, TREE))
    changed = leaf(L3, G, 8, (2, 6))
    await index.apply(repo, ROOT_1, MerkleUpdate(ROOT_2, (changed,), deleted=(D,)))
    assert await index.root(other) == ROOT_1
    whole = await index.children(other, [None, D, F, G])
    assert whole == {
        None: (view(TREE[4]), view(TREE[0])),
        D: (view(TREE[1]),),
        F: (view(TREE[2]), view(TREE[3])),
        G: (view(TREE[5]),),
    }


def ref(
    repository_id: uuid.UUID, node: MerkleNodeUpdate, *, file_id: str | None = None
) -> ChunkRef:
    """`node`'s chunk as the delta asked for it (its file is its parent)."""
    assert node.lines is not None
    return ChunkRef(
        repository_id, file_id or node.parent_id or F, *node.lines, node.hash, node.node_id
    )


def result(
    repository_id: uuid.UUID,
    node: MerkleNodeUpdate,
    *,
    reason: RejectionReason | None = None,
    file_id: str | None = None,
) -> LeafResult:
    """The indexer's result for `node` as it was uploaded (its file is its parent)."""
    chunk = ref(repository_id, node, file_id=file_id)
    if reason is not None:
        return LeafResult(chunk, reason=reason)
    vector = vector_contract.axis((0, 1.0))
    return LeafResult(chunk, embedded=VectorRecord(chunk, vector, vector_contract.MODEL))


async def check_a_pending_leaf_settles_once(
    index: MerkleIndex, repositories: tuple[uuid.UUID, uuid.UUID]
) -> None:
    repo, _ = repositories
    await index.apply(repo, None, MerkleUpdate(ROOT_1, TREE))
    first, second = TREE[2], TREE[3]
    assert await index.settle(result(repo, first)) == "settled"
    assert await index.settle(result(repo, first)) == "already_settled"
    assert await index.settle(result(repo, second, reason="secret_found")) == "settled"
    assert await index.settle(result(repo, second)) == "already_settled"


async def check_a_late_result_for_replaced_or_deleted_content_is_not_pending(
    index: MerkleIndex, repositories: tuple[uuid.UUID, uuid.UUID]
) -> None:
    repo, _ = repositories
    await index.apply(repo, None, MerkleUpdate(ROOT_1, TREE))
    changed = leaf(L1, F, 9, (1, 10))
    await index.apply(repo, ROOT_1, MerkleUpdate(ROOT_2, (changed,), deleted=(G,)))
    assert await index.settle(result(repo, TREE[2])) == "not_pending"  # the old content
    assert await index.settle(result(repo, TREE[5])) == "not_pending"  # deleted
    assert await index.settle(result(repo, changed)) == "settled"


async def check_a_result_must_match_its_leaf(
    index: MerkleIndex, repositories: tuple[uuid.UUID, uuid.UUID]
) -> None:
    repo, other = repositories
    await index.apply(repo, None, MerkleUpdate(ROOT_1, TREE))
    first = TREE[2]
    assert await index.settle(result(repo, first, file_id=G)) == "not_pending"
    assert await index.settle(result(other, first)) == "not_pending"  # another repository
    assert await index.settle(result(uuid.uuid4(), first)) == "not_pending"  # unknown one
    unknown = leaf(hexid(0x99), F, 3, (1, 10))
    assert await index.settle(result(repo, unknown)) == "not_pending"
    not_a_leaf = leaf(F, D, 2, (1, 10))  # the file node's id and hash
    assert await index.settle(result(repo, not_a_leaf)) == "not_pending"
    assert await index.settle(result(repo, first)) == "settled"


async def check_an_unchanged_leaf_keeps_its_state(
    index: MerkleIndex, repositories: tuple[uuid.UUID, uuid.UUID]
) -> None:
    repo, _ = repositories
    await index.apply(repo, None, MerkleUpdate(ROOT_1, TREE))
    first = TREE[2]
    assert await index.settle(result(repo, first)) == "settled"
    moved = leaf(L1, F, 3, (5, 14))  # same content, four lines down
    await index.apply(repo, ROOT_1, MerkleUpdate(ROOT_2, (moved,)))
    assert await index.settle(result(repo, moved)) == "already_settled"
    edited = leaf(L1, F, 7, (5, 14))
    await index.apply(repo, ROOT_2, MerkleUpdate(ROOT_3, (edited,)))
    assert await index.settle(result(repo, edited)) == "settled"


async def check_only_pending_leaves_with_their_hash_and_file_may_be_uploaded(
    index: MerkleIndex, repositories: tuple[uuid.UUID, uuid.UUID]
) -> None:
    repo, other = repositories
    await index.apply(repo, None, MerkleUpdate(ROOT_1, TREE))
    first, second, third = TREE[2], TREE[3], TREE[5]
    assert await index.settle(result(repo, third)) == "settled"
    stale = leaf(L2, F, 99, (12, 20))  # a hash the store does not have
    refs = [
        ref(repo, first),
        ref(repo, stale),
        ref(repo, second, file_id=G),  # another file
        ref(repo, third),  # indexed, no longer pending
        ref(other, first),  # another repository's ref
        ChunkRef(repo, D, 1, 1, TREE[1].hash, F),  # an inner node
    ]
    assert await index.pending(repo, refs) == {L1}
    assert await index.pending(other, [ref(other, first)]) == frozenset()
    assert await index.pending(repo, []) == frozenset()
    with pytest.raises(InvalidValueError):
        await index.pending(repo, [ref(repo, first)] * (MAX_CHUNKS_PER_UPLOAD + 1))


CHECKS: tuple[Check, ...] = (
    check_a_tree_is_stored_and_read_by_level,
    check_a_stale_root_changes_nothing,
    check_a_deleted_subtree_goes_whole,
    check_a_node_is_replaced_and_limits_hold,
    check_every_node_stays_reachable,
    check_repositories_with_the_same_ids_stay_apart,
    check_a_pending_leaf_settles_once,
    check_a_late_result_for_replaced_or_deleted_content_is_not_pending,
    check_a_result_must_match_its_leaf,
    check_an_unchanged_leaf_keeps_its_state,
    check_only_pending_leaves_with_their_hash_and_file_may_be_uploaded,
)
