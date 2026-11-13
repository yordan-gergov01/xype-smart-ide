"""Contract of `MerkleIndex` (ADR-0018): the fake (unit) and the Postgres adapter
(integration) both run it. `repositories` are two registered repositories that were
never synced (root None)."""

import uuid
from collections.abc import Awaitable, Callable

import pytest

from backend.services.indexing import MerkleIndex
from common_lib.errors import InvalidValueError
from common_lib.merkle import (
    MAX_NODES_PER_CALL,
    MerkleConflictError,
    MerkleNodeUpdate,
    MerkleNodeView,
    MerkleUpdate,
)


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


CHECKS: tuple[Check, ...] = (
    check_a_tree_is_stored_and_read_by_level,
    check_a_stale_root_changes_nothing,
    check_a_deleted_subtree_goes_whole,
    check_a_node_is_replaced_and_limits_hold,
    check_every_node_stays_reachable,
    check_repositories_with_the_same_ids_stay_apart,
)
