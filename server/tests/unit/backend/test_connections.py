from support.backend import make_settings

from backend.core.connections import create_database, create_redis_client
from backend.main import _default_probes


async def test_reads_use_the_primary_without_a_replica() -> None:
    database = create_database(make_settings())
    assert database.read_engine is database.write_engine
    async with database.read_sessions() as session:
        assert session.bind is database.write_engine
    await database.dispose()


async def test_reads_use_the_replica_when_configured() -> None:
    database = create_database(make_settings(db_read_host="replica.invalid"))
    assert database.read_engine is not database.write_engine
    assert database.read_engine.url.host == "replica.invalid"
    assert database.write_engine.url.host == "db.invalid"
    await database.dispose()


def test_readiness_also_probes_a_separate_replica() -> None:
    harness_names = [
        p.name
        for p in _default_probes(
            make_settings(db_read_host="replica.invalid"),
            create_database(make_settings(db_read_host="replica.invalid")),
            create_redis_client(make_settings()),
        )
    ]
    assert harness_names == ["database", "database_read", "redis", "broker"]
