"""Login, refresh, authentication and the admin flows over the fakes (ADR-0014)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from support.backend import FakeAuth, make_settings
from support.clock import FakeClock

from backend.core.auth import auth_service, client_admin
from backend.core.settings import BackendSettings
from backend.services.auth import AuthService, ClientAdmin, UnknownClientError
from backend.services.auth.access_tokens import CLOCK_SKEW_SECONDS
from common_lib.auth import (
    AccessClaims,
    ConflictError,
    Credential,
    InvalidCredentialsError,
    InvalidTokenError,
    NewClient,
    RateLimitedError,
    Revocation,
)
from common_lib.errors import DependencyUnavailableError
from common_lib.secret_bytes import SecretBytes
from common_lib.security import (
    REVOCATION_MEMORY_SECONDS,
    KeyRevokedError,
    SessionKeyRecord,
    new_key,
)
from common_lib.settings import new_keyed_secret


class World:
    def __init__(self, **settings: object) -> None:
        self.settings = make_settings(**settings)
        self.clock = FakeClock(datetime.now(UTC))
        self.fakes = FakeAuth(self.settings, clock=self.clock)
        self.service: AuthService = auth_service(self.settings, self.fakes.deps, self.clock)
        self.admin: ClientAdmin = client_admin(self.settings, self.fakes.deps, self.clock)

    async def client(self, team: str = "Web") -> NewClient:
        if await self.fakes.users.find_team(team) is None:
            await self.admin.create_team(team)
        return await self.admin.create_client(team=team, email=f"{team}@x.test", display_name="D")


async def mint(world: World, claims: AccessClaims) -> str:
    """Stores a session key as `SessionKeyService` would; returns its id."""
    key_id = str(uuid.uuid4())
    user, family = str(claims.principal.user_id), str(claims.family_id)
    record = SessionKeyRecord(key_id, user, family, world.clock.now())
    await world.fakes.keys.put(
        record, new_key(), ttl_seconds=60, max_active=8, credentials_issued_at=claims.issued_at
    )
    return key_id


def credential(client: NewClient, secret: str | None = None) -> Credential:
    value = (secret or client.client_secret).encode()
    return Credential("client_credentials", client.client_id, SecretBytes(value))


@pytest.fixture
def world() -> World:
    return World()


async def test_login_issues_a_pair_whose_access_token_authenticates(world: World) -> None:
    client = await world.client()
    pair = await world.service.login(credential(client))
    claims = await world.service.authenticate(pair.access_token)
    assert claims.principal.user_id == client.user_id
    assert pair.expires_in_seconds == 900
    # Only hashes are stored: neither the secret nor the refresh token.
    stored = repr(world.fakes.users.users) + repr(world.fakes.tokens.tokens)
    assert client.client_secret not in stored
    assert pair.refresh_token not in stored


@pytest.mark.parametrize("case", ["wrong-secret", "unknown-id", "disabled"])
async def test_login_fails_the_same_way(world: World, case: str) -> None:
    client = await world.client()
    if case == "disabled":
        await world.admin.disable_client(client.client_id)
    attempt = {
        "wrong-secret": credential(client, "xcs_" + "A" * 43),
        "unknown-id": Credential("client_credentials", "xc_" + "0" * 16, SecretBytes(b"x")),
        "disabled": credential(client),
    }[case]
    with pytest.raises(InvalidCredentialsError):
        await world.service.login(attempt)


async def test_failed_logins_back_off_per_client_id_only(world: World) -> None:
    client, other = await world.client("A"), await world.client("B")
    for _ in range(10):
        with pytest.raises(InvalidCredentialsError):
            await world.service.login(credential(client, "xcs_wrong"))
    with pytest.raises(RateLimitedError) as backoff:
        await world.service.login(credential(client))
    assert 0 < backoff.value.retry_after_seconds <= 6
    await world.service.login(credential(other))
    world.clock.advance(timedelta(seconds=6))
    await world.service.login(credential(client))


async def test_refresh_rotates_and_reuse_revokes_the_family(world: World) -> None:
    client = await world.client()
    first = await world.service.login(credential(client))
    second = await world.service.refresh(first.refresh_token)
    assert second.refresh_token != first.refresh_token
    with pytest.raises(InvalidTokenError):
        await world.service.refresh(first.refresh_token)
    claims = await world.service.authenticate(first.access_token)
    assert world.fakes.revocations.published == [Revocation("family", claims.family_id)]
    with pytest.raises(InvalidTokenError):
        await world.service.refresh(second.refresh_token)
    # The family's session keys went too, and it can mint no new ones.
    with pytest.raises(KeyRevokedError):
        await mint(world, claims)


async def test_two_windows_refresh_independently(world: World) -> None:
    client = await world.client()
    window_a = await world.service.login(credential(client))
    window_b = await world.service.login(credential(client))
    await world.service.refresh(window_a.refresh_token)
    await world.service.refresh(window_b.refresh_token)
    assert world.fakes.revocations.published == []


async def test_refresh_refuses_unknown_expired_and_disabled(world: World) -> None:
    client = await world.client()
    pair = await world.service.login(credential(client))
    with pytest.raises(InvalidTokenError):
        await world.service.refresh("xrt_unknown")
    world.clock.advance(timedelta(days=31))
    with pytest.raises(InvalidTokenError):
        await world.service.refresh(pair.refresh_token)
    fresh = await world.service.login(credential(client))
    await world.admin.disable_client(client.client_id)
    with pytest.raises(InvalidTokenError):
        await world.service.refresh(fresh.refresh_token)


async def test_request_201_within_a_minute_is_refused(world: World) -> None:
    pair = await world.service.login(credential(await world.client()))
    for _ in range(200):
        await world.service.authenticate(pair.access_token)
    with pytest.raises(RateLimitedError):
        await world.service.authenticate(pair.access_token)
    world.clock.advance(timedelta(seconds=1))
    await world.service.authenticate(pair.access_token)


async def test_fails_closed_without_the_limiter_store(world: World) -> None:
    pair = await world.service.login(credential(await world.client()))
    world.fakes.limiter.down = True
    with pytest.raises(DependencyUnavailableError):
        await world.service.authenticate(pair.access_token)


async def test_disable_and_rotate_revoke_tokens_and_tell_the_replicas(world: World) -> None:
    client = await world.client()
    pair = await world.service.login(credential(client))
    claims = await world.service.authenticate(pair.access_token)
    key_id = await mint(world, claims)
    world.clock.advance(timedelta(seconds=1))
    rotated = await world.admin.rotate_secret(client.client_id)
    assert world.fakes.keys.holds(key_id) is None
    with pytest.raises(KeyRevokedError):
        await mint(world, claims)
    with pytest.raises(InvalidCredentialsError):
        await world.service.login(credential(client))
    with pytest.raises(InvalidTokenError):
        await world.service.refresh(pair.refresh_token)
    # A login within the clock skew of the rotation mints nothing; after it, it does.
    soon = await world.service.login(credential(rotated))
    with pytest.raises(KeyRevokedError):
        await mint(world, await world.service.authenticate(soon.access_token))
    world.clock.advance(timedelta(seconds=31))
    fresh = await world.service.login(credential(rotated))
    await mint(world, await world.service.authenticate(fresh.access_token))
    await world.admin.disable_client(client.client_id)
    user = Revocation("user", client.user_id)
    assert world.fakes.revocations.published[:1] == [user]
    assert user in world.fakes.revocations.published[1:]


async def test_admin_refuses_unknown_teams_clients_and_duplicates(world: World) -> None:
    with pytest.raises(UnknownClientError):
        await world.admin.create_client(team="none", email="a@x.test", display_name="A")
    for action in (world.admin.disable_client, world.admin.rotate_secret):
        with pytest.raises(UnknownClientError):
            await action("xc_" + "f" * 16)
    await world.admin.create_team("Web")
    with pytest.raises(ConflictError):
        await world.admin.create_team("Web")


async def test_secrets_and_tokens_survive_a_pepper_rotation(world: World) -> None:
    client = await world.client()
    pair = await world.service.login(credential(client))
    settings = make_settings(
        auth_pepper_current=new_keyed_secret("pepper-2"),
        auth_pepper_previous=world.settings.auth_pepper_current.get_secret_value(),
    )
    rotated = FakeAuth(settings, users=world.fakes.users)
    rotated.tokens = world.fakes.tokens
    service = auth_service(settings, rotated.deps, world.clock)
    await service.login(credential(client))
    await service.refresh(pair.refresh_token)


async def test_reuse_tells_the_replicas_even_without_the_key_store(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    pair = await world.service.login(credential(await world.client()))
    await world.service.refresh(pair.refresh_token)

    async def down(_: str) -> None:
        raise DependencyUnavailableError("keystore")

    monkeypatch.setattr(world.fakes.keys, "revoke_family", down)
    with pytest.raises(DependencyUnavailableError):
        await world.service.refresh(pair.refresh_token)
    claims = await world.service.authenticate(pair.access_token)
    assert world.fakes.revocations.published == [Revocation("family", claims.family_id)]


def test_revocations_outlive_every_access_token() -> None:
    """The tombstone must cover the longest access token plus the skew (ADR-0014)."""
    ttl = BackendSettings.model_fields["auth_access_ttl_s"].metadata
    longest = max(m.le for m in ttl if hasattr(m, "le"))
    assert longest + 2 * CLOCK_SKEW_SECONDS <= REVOCATION_MEMORY_SECONDS
