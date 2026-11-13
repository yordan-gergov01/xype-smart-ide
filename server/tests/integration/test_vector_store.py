"""`PgVectorStore` against Postgres with pgvector (migrated, app role, TLS): the shared
contract, plus recall under a selective repository filter through the HNSW index with
iterative scans (ADR-0020)."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from support.contracts import vectors as contract
from support.database import Database
from support.stores import app_sessions, register_repositories

from backend.dal.retrieval import PgVectorStore, search_query

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("check", contract.CHECKS, ids=lambda c: c.__name__)
async def test_pgvector_meets_the_contract(
    index_sessions: async_sessionmaker[AsyncSession], check: contract.Check
) -> None:
    first, second = await register_repositories(index_sessions)
    await check(PgVectorStore(index_sessions, index_sessions), (first, second))


async def _seed_busy_and_quiet(store: PgVectorStore, busy: uuid.UUID, quiet: uuid.UUID) -> None:
    for batch in range(2):
        await store.upsert(
            busy,
            [
                contract.record(busy, batch * 200 + n, contract.axis((0, 1.0), (1 + n % 50, 0.1)))
                for n in range(200)
            ],
        )
    await store.upsert(
        quiet,
        # Same neighbourhood, a little farther: HNSW links them to the busy rows.
        [contract.record(quiet, n, contract.axis((0, 1.0), (1 + n, 0.6))) for n in range(5)],
    )


async def test_a_selective_filter_still_returns_k_rows_through_the_index(
    migrated_database: Database, index_sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The busy repository's rows are all closer to the query than the quiet one's, so a
    plain HNSW scan with `ef_search = 10` keeps fewer than `k` of the quiet repository's
    rows after filtering (the control). Iterative scans keep going until `k` rows match."""
    busy, quiet = await register_repositories(index_sessions)
    await _seed_busy_and_quiet(PgVectorStore(index_sessions, index_sessions), busy, quiet)
    query = {"r": quiet, "q": str(contract.axis((0, 1.0)))}
    limited = (
        "SELECT count(*) FROM (SELECT id FROM code_chunks WHERE repository_id = :r"
        " ORDER BY vector_embedding <=> CAST(:q AS vector) LIMIT 5) AS hits"
    )
    # The planner would rightly sort the few rows of a tiny repository exactly; these
    # settings make it use the HNSW index, the path a large repository takes.
    index_only = app_sessions(migrated_database, enable_seqscan="off", enable_sort="off")
    try:
        async with index_only.begin() as session:
            statement = search_query(
                contract.axis((0, 1.0)), repository_ids=[quiet], model=contract.MODEL, k=5
            )
            compiled = statement.compile(
                dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]  # untyped in SQLAlchemy
                compile_kwargs={"literal_binds": True},
            )
            plan = (await session.execute(text(f"EXPLAIN {compiled}"))).scalars()
            assert "idx_code_chunks_embedding_hnsw" in "\n".join(plan)
        async with index_only.begin() as session:
            # The control: without iterative scans the filter leaves fewer than 5 rows.
            await session.execute(text("SELECT set_config('hnsw.ef_search', '10', true)"))
            assert (await session.execute(text(limited), query)).scalar_one() < 5
        hits = await PgVectorStore(index_only, index_only, ef_search=10).search(
            contract.axis((0, 1.0)), repository_ids=[quiet], model=contract.MODEL, k=5
        )
    finally:
        await index_only.kw["bind"].dispose()
    assert {h.chunk.repository_id for h in hits} == {quiet}
    assert len(hits) == 5
