"""`PgMerkleIndex` against Postgres (migrated, app role, TLS): the shared contract, and
what only the database shows: a deleted subtree takes its chunks, a moved leaf moves its
stored range without a new vector, writes are batched, and two syncs of one repository
cannot both apply (ADR-0018)."""

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from support.contracts import merkle as contract
from support.contracts import vectors as vector_contract
from support.stores import register_repositories

from backend.dal.indexing import PgMerkleIndex
from backend.dal.retrieval import PgVectorStore
from common_lib.merkle import MerkleConflictError, MerkleNodeUpdate, MerkleUpdate

pytestmark = pytest.mark.integration

HEX = contract.hexid


@pytest.mark.parametrize("check", contract.CHECKS, ids=lambda c: c.__name__)
async def test_postgres_meets_the_contract(
    index_sessions: async_sessionmaker[AsyncSession], check: contract.Check
) -> None:
    first, second = await register_repositories(index_sessions)
    await check(PgMerkleIndex(index_sessions), (first, second))


async def _lines(store: PgVectorStore, repository_id: uuid.UUID) -> dict[str, tuple[int, int]]:
    hits = await store.search(
        vector_contract.axis((0, 1.0)),
        repository_ids=[repository_id],
        model=vector_contract.MODEL,
        k=10,
    )
    return {h.chunk.leaf_node_id: (h.chunk.line_start, h.chunk.line_end) for h in hits}


async def test_chunks_follow_their_leaves(
    index_sessions: async_sessionmaker[AsyncSession],
) -> None:
    (repo,) = await register_repositories(index_sessions, count=1)
    index, store = PgMerkleIndex(index_sessions), PgVectorStore(index_sessions, index_sessions)
    await index.apply(repo, None, MerkleUpdate(contract.ROOT_1, contract.TREE))
    # Chunks for L1 and L3 as the indexer will store them (slice 8); L2 was rejected.
    await store.upsert(
        repo,
        [
            vector_contract.record(repo, 0x11, vector_contract.axis((0, 1.0)), lines=(1, 10)),
            vector_contract.record(repo, 0x13, vector_contract.axis((0, 1.0)), lines=(1, 5)),
        ],
    )
    moved = contract.leaf(contract.L3, contract.G, 6, (4, 8))  # same hash, three lines down
    rejected = contract.leaf(contract.L2, contract.F, 4, (30, 40))
    await index.apply(repo, contract.ROOT_1, MerkleUpdate(contract.ROOT_2, (moved, rejected)))
    assert await _lines(store, repo) == {contract.L1: (1, 10), contract.L3: (4, 8)}
    deleted = MerkleUpdate(contract.ROOT_3, (), deleted=(contract.D,))
    await index.apply(repo, contract.ROOT_2, deleted)
    assert await _lines(store, repo) == {contract.L3: (4, 8)}


async def test_chunks_of_another_repository_with_the_same_ids_stay(
    index_sessions: async_sessionmaker[AsyncSession],
) -> None:
    repo, other = await register_repositories(index_sessions)
    index, store = PgMerkleIndex(index_sessions), PgVectorStore(index_sessions, index_sessions)
    for repository_id in (repo, other):
        await index.apply(repository_id, None, MerkleUpdate(contract.ROOT_1, contract.TREE))
        await store.upsert(
            repository_id,
            [
                vector_contract.record(repository_id, n, vector_contract.axis((0, 1.0)))
                for n in (0x11, 0x13)
            ],
        )
    moved = contract.leaf(contract.L3, contract.G, 6, (7, 11))
    await index.apply(
        repo, contract.ROOT_1, MerkleUpdate(contract.ROOT_2, (moved,), deleted=(contract.D,))
    )
    assert await _lines(store, repo) == {contract.L3: (7, 11)}
    assert await _lines(store, other) == {contract.L1: (1, 5), contract.L3: (1, 5)}


async def test_a_large_tree_is_written_in_batches(
    index_sessions: async_sessionmaker[AsyncSession],
) -> None:
    (repo,) = await register_repositories(index_sessions, count=1)
    index = PgMerkleIndex(index_sessions)
    folder = MerkleNodeUpdate(HEX(1), None, HEX(2), is_leaf=False)
    leaves = [
        MerkleNodeUpdate(
            HEX(0x1000 + n), folder.node_id, HEX(n + 3), is_leaf=True, lines=(n + 1,) * 2
        )
        for n in range(2500)
    ]
    await index.apply(repo, None, MerkleUpdate(contract.ROOT_1, (folder, *leaves)))
    children = (await index.children(repo, [folder.node_id]))[folder.node_id]
    assert [c.node_id for c in children] == [leaf.node_id for leaf in leaves]
    await index.apply(repo, contract.ROOT_1, MerkleUpdate(contract.ROOT_2, (), deleted=(HEX(1),)))
    assert await index.children(repo, [None, folder.node_id]) == {None: (), folder.node_id: ()}


async def test_two_syncs_of_one_repository_cannot_both_apply(
    index_sessions: async_sessionmaker[AsyncSession],
) -> None:
    (repo,) = await register_repositories(index_sessions, count=1)
    index = PgMerkleIndex(index_sessions)
    await index.apply(repo, None, MerkleUpdate(contract.ROOT_1, contract.TREE))
    results = await asyncio.gather(
        index.apply(
            repo, contract.ROOT_1, MerkleUpdate(contract.ROOT_2, (), deleted=(contract.D,))
        ),
        index.apply(
            repo, contract.ROOT_1, MerkleUpdate(contract.ROOT_3, (), deleted=(contract.G,))
        ),
        return_exceptions=True,
    )
    assert sorted(type(r).__name__ for r in results) == ["MerkleConflictError", "NoneType"]
    winner = contract.ROOT_2 if results[0] is None else contract.ROOT_3
    assert await index.root(repo) == winner
    assert isinstance(next(r for r in results if r is not None), MerkleConflictError)
