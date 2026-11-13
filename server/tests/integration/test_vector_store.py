"""`PgVectorStore` against Postgres with pgvector (migrated, app role, TLS): the shared
contract, plus recall under a selective repository filter through the HNSW index with
iterative scans (ADR-0020)."""

import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy import insert, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from support.contracts import vectors as contract
from support.database import Database, alembic, postgres
from support.tls import ca_file

from backend.dal.retrieval import PgVectorStore, search_query
from common_lib.models import Repository, Team
from common_lib.models.connection import postgres_url
from common_lib.tls import client_context

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def database() -> Iterator[Database]:
    with postgres() as db:
        alembic(db, ("upgrade", "head"))
        yield db


def _sessions(database: Database, **server_settings: str) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(
        postgres_url(database.app_settings()),
        connect_args={"ssl": client_context(ca_file()), "server_settings": server_settings},
    )
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def sessions(database: Database) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    sessions = _sessions(database)
    yield sessions
    await sessions.kw["bind"].dispose()


async def _repositories(
    sessions: async_sessionmaker[AsyncSession], count: int = 2
) -> list[uuid.UUID]:
    async with sessions.begin() as session:
        team = (
            await session.execute(insert(Team).values(name=uuid.uuid4().hex).returning(Team.id))
        ).scalar_one()
        return [
            (
                await session.execute(
                    insert(Repository)
                    .values(team_id=team, name=uuid.uuid4().hex, hmac_key_id="hk")
                    .returning(Repository.id)
                )
            ).scalar_one()
            for _ in range(count)
        ]


@pytest.mark.parametrize("check", contract.CHECKS, ids=lambda c: c.__name__)
async def test_pgvector_meets_the_contract(
    sessions: async_sessionmaker[AsyncSession], check: contract.Check
) -> None:
    first, second = await _repositories(sessions)
    await check(PgVectorStore(sessions, sessions), (first, second))


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
    database: Database, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The busy repository's rows are all closer to the query than the quiet one's, so a
    plain HNSW scan with `ef_search = 10` keeps fewer than `k` of the quiet repository's
    rows after filtering (the control). Iterative scans keep going until `k` rows match."""
    busy, quiet = await _repositories(sessions)
    await _seed_busy_and_quiet(PgVectorStore(sessions, sessions), busy, quiet)
    query = {"r": quiet, "q": str(contract.axis((0, 1.0)))}
    limited = (
        "SELECT count(*) FROM (SELECT id FROM code_chunks WHERE repository_id = :r"
        " ORDER BY vector_embedding <=> CAST(:q AS vector) LIMIT 5) AS hits"
    )
    # The planner would rightly sort the few rows of a tiny repository exactly; these
    # settings make it use the HNSW index, the path a large repository takes.
    index_only = _sessions(database, enable_seqscan="off", enable_sort="off")
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
