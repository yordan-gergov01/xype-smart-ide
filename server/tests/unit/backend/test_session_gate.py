"""`SessionGate` (ADR-0013, ADR-0015): who may open a stream, with which session key and
which repositories, and the checks on every later message. Includes the cross-team
negative test the phase 2 spec asks of every message that names a repository."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import ValidationError
from support.backend import FakeAuth, make_settings
from support.clock import FakeClock
from support.session import FakeRepositoryAccess, assert_only_the_teams_repositories

from backend.core.auth import auth_service, client_admin, session_key_service
from backend.core.session import session_gate
from backend.services.session import HelloRequest, RefusedError, SessionGate
from common_lib.auth import AccessClaims, Credential, NewClient, RateLimitedError
from common_lib.secret_bytes import SecretBytes


class World:
    def __init__(self, **settings: object) -> None:
        self.settings = make_settings(**settings)
        self.clock = FakeClock(datetime.now(UTC))
        self.fakes = FakeAuth(self.settings, clock=self.clock)
        self.repositories = FakeRepositoryAccess()
        self.clients: dict[str, NewClient] = {}
        self.auth = auth_service(self.settings, self.fakes.deps, self.clock)
        self.gate: SessionGate = session_gate(
            self.settings, self.auth, self.fakes.deps, self.repositories, self.clock
        )

    async def claims(self, team: str = "Web") -> AccessClaims:
        admin = client_admin(self.settings, self.fakes.deps, self.clock)
        if await self.fakes.users.find_team(team) is None:
            await admin.create_team(team)
        client = await admin.create_client(team=team, email=f"{team}@x.test", display_name="D")
        self.clients[team] = client
        return await self.window(team)

    async def window(self, team: str) -> AccessClaims:
        """A new login of the team's client: another IDE window, another token family."""
        client = self.clients[team]
        secret = SecretBytes(client.client_secret.encode())
        pair = await self.auth.login(Credential("client_credentials", client.client_id, secret))
        return await self.gate.open(pair.access_token)

    async def key(self, claims: AccessClaims) -> str:
        public = (
            X25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        )
        service = session_key_service(self.settings, self.fakes.deps, self.clock)
        return (await service.mint(claims, public)).key_id


def hello(key_id: str, *repositories: uuid.UUID, **roots: str) -> HelloRequest:
    return HelloRequest(key_id, tuple(str(r) for r in repositories), roots)


@pytest.fixture
def world() -> World:
    return World()


async def test_hello_binds_the_key_and_the_teams_repositories(world: World) -> None:
    claims = await world.claims()
    repository = world.repositories.register(claims.principal.team_id)
    key_id = await world.key(claims)
    session = await world.gate.hello(claims, hello(key_id, repository), "session-a")
    assert session.repository_ids == {repository}
    assert session.key.key_id == key_id
    await world.gate.message(session, [str(repository)])
    # The key now belongs to this stream only.
    other = await world.claims("Other")
    with pytest.raises(RefusedError, match="another stream"):
        await world.gate.hello(claims, hello(key_id), "session-b")
    assert other.principal.team_id != claims.principal.team_id


async def test_refuses_another_teams_repository(world: World) -> None:
    """Cross-team negative test: Hello and every later message."""
    mine, theirs = await world.claims("Mine"), await world.claims("Theirs")
    own = world.repositories.register(mine.principal.team_id)
    foreign = world.repositories.register(theirs.principal.team_id)
    with pytest.raises(RefusedError, match="not accessible"):
        await world.gate.hello(mine, hello(await world.key(mine), own, foreign), "s-1")
    session = await world.gate.hello(mine, hello(await world.key(mine), own), "s-2")
    with pytest.raises(RefusedError, match="not accessible"):
        await world.gate.message(session, [str(foreign)])
    with pytest.raises(RefusedError, match="not accessible"):
        await world.gate.message(session, [str(uuid.uuid4())])


async def test_refuses_a_key_of_another_user_family_or_age(world: World) -> None:
    claims, stranger = await world.claims("A"), await world.claims("B")
    with pytest.raises(RefusedError, match="not yours"):
        await world.gate.hello(claims, hello(await world.key(stranger)), "s-1")
    # Another window (token family) of the same user.
    window = await world.window("A")
    assert window.family_id != claims.family_id
    with pytest.raises(RefusedError, match="not yours"):
        await world.gate.hello(claims, hello(await world.key(window)), "s-2")
    for unknown in (str(uuid.uuid4()), "../../etc", ""):
        with pytest.raises(RefusedError):
            await world.gate.hello(claims, hello(unknown), "s-3")
    key_id = await world.key(claims)
    session = await world.gate.hello(claims, hello(key_id), "s-4")
    world.clock.advance(timedelta(hours=12))
    with pytest.raises(RefusedError, match="expired"):
        await world.gate.message(session)


@pytest.mark.parametrize(
    "case", ["bad-id", "duplicate", "root-not-listed", "root-not-hex", "too-many"]
)
async def test_refuses_a_malformed_hello(case: str) -> None:
    world = World(stream_max_repositories=2)
    claims = await world.claims()
    team = claims.principal.team_id
    a, b, c = (world.repositories.register(team) for _ in range(3))
    key_id = await world.key(claims)
    request = {
        "bad-id": HelloRequest(key_id, ("not-a-uuid",), {}),
        "duplicate": HelloRequest(key_id, (str(a), str(a)), {}),
        "root-not-listed": HelloRequest(key_id, (str(a),), {str(b): "0" * 64}),
        "root-not-hex": HelloRequest(key_id, (str(a),), {str(a): "Z" * 64}),
        "too-many": hello(key_id, a, b, c),
    }[case]
    with pytest.raises(RefusedError, match="malformed"):
        await world.gate.hello(claims, request, "s-1")


async def test_roots_of_listed_repositories_are_accepted(world: World) -> None:
    claims = await world.claims()
    repository = world.repositories.register(claims.principal.team_id)
    key_id = await world.key(claims)
    request = hello(key_id, repository, **{str(repository): "ab" * 32})
    await world.gate.hello(claims, request, "s-1")


async def test_messages_count_against_the_users_rate_limit() -> None:
    world = World(rate_limit_per_minute=4)
    claims = await world.claims()  # the login does not count; opening does (1)
    session = await world.gate.hello(claims, hello(await world.key(claims)), "s-1")  # 2
    await world.gate.message(session)  # 3
    await world.gate.message(session)  # 4
    with pytest.raises(RateLimitedError):
        await world.gate.message(session)


async def test_fake_repository_access_meets_the_contract() -> None:
    fake = FakeRepositoryAccess()

    async def register(team: uuid.UUID) -> uuid.UUID:
        return fake.register(team)

    await assert_only_the_teams_repositories(fake, register, (uuid.uuid4(), uuid.uuid4()))


def test_settings_refuse_an_idle_timeout_shorter_than_two_heartbeats() -> None:
    with pytest.raises(ValidationError, match="twice the heartbeat"):
        make_settings(stream_heartbeat_interval_s=30, stream_idle_timeout_s=59)
    make_settings(stream_heartbeat_interval_s=30, stream_idle_timeout_s=60)
