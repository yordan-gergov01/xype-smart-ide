"""In-memory fakes of the auth seams (rule 03); the shared contracts in
support/contracts/auth.py hold them to the same behaviour as the real adapters."""

import math
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from backend.dal.auth.redis_adapters import gcra_interval_ms
from common_lib.auth import (
    ROLES,
    ConflictError,
    NewRefreshToken,
    NotFound,
    Principal,
    RateDecision,
    Reused,
    Revocation,
    Role,
    Rotated,
    Rotation,
    StoredCredentials,
)
from common_lib.errors import DependencyUnavailableError
from common_lib.settings import new_keyed_secret

# Fresh per test session, so nothing in the repository is a usable key.
SIGNING_KEY = new_keyed_secret("test-signing")
OTHER_SIGNING_KEY = new_keyed_secret("test-signing-old")
PEPPER = new_keyed_secret("test-pepper")


@dataclass
class _User:
    user_id: uuid.UUID
    team_id: uuid.UUID
    email: str
    role: Role
    client_id: str
    secret_hash: str
    is_active: bool = True
    last_login: datetime | None = None


class FakeUserStore:
    def __init__(self) -> None:
        self.teams: dict[str, uuid.UUID] = {}
        self.users: dict[str, _User] = {}

    async def find_credentials(self, client_id: str) -> StoredCredentials | None:
        user = self.users.get(client_id)
        if user is None:
            return None
        principal = Principal(user.user_id, user.team_id, ROLES[user.role])
        return StoredCredentials(principal, user.secret_hash, user.is_active)

    async def record_login(self, user_id: uuid.UUID, at: datetime) -> None:
        for user in self.users.values():
            if user.user_id == user_id:
                user.last_login = at

    async def create_team(self, name: str) -> uuid.UUID:
        if name in self.teams:
            raise ConflictError("already exists")
        self.teams[name] = uuid.uuid4()
        return self.teams[name]

    async def find_team(self, name: str) -> uuid.UUID | None:
        return self.teams.get(name)

    async def create_user(
        self,
        *,
        team_id: uuid.UUID,
        email: str,
        display_name: str,
        role: Role,
        client_id: str,
        secret_hash: str,
    ) -> uuid.UUID:
        taken = client_id in self.users or any(u.email == email for u in self.users.values())
        if taken or team_id not in self.teams.values():
            raise ConflictError("already exists")
        user = _User(uuid.uuid4(), team_id, email, role, client_id, secret_hash)
        self.users[client_id] = user
        return user.user_id

    async def set_active(self, client_id: str, *, active: bool) -> uuid.UUID | None:
        user = self.users.get(client_id)
        if user is not None:
            user.is_active = active
        return user.user_id if user else None

    async def set_secret_hash(self, client_id: str, secret_hash: str) -> uuid.UUID | None:
        user = self.users.get(client_id)
        if user is not None:
            user.secret_hash = secret_hash
        return user.user_id if user else None


@dataclass(frozen=True)
class _Token:
    token: NewRefreshToken
    revoked_at: datetime | None = None


class FakeRefreshTokenStore:
    """Knows users only through the `FakeUserStore` it is given (team, role, active)."""

    def __init__(self, users: FakeUserStore) -> None:
        self._users = users
        self.tokens: dict[str, _Token] = {}

    async def add(self, token: NewRefreshToken, *, secret_hash: str) -> bool:
        user = self._user(token.user_id)
        if user is None or not user.is_active or user.secret_hash != secret_hash:
            return False
        self._store(token)
        return True

    def _store(self, token: NewRefreshToken) -> None:
        if token.token_hash in self.tokens:
            raise ConflictError("already exists")
        self.tokens[token.token_hash] = _Token(token)

    async def rotate(
        self,
        token_hashes: Sequence[str],
        new_token_hash: str,
        *,
        now: datetime,
        refresh_ttl_seconds: int,
    ) -> Rotation:
        found = next((self.tokens[h] for h in token_hashes if h in self.tokens), None)
        if found is None:
            return NotFound()
        token = found.token
        if found.revoked_at is not None:
            self._revoke(lambda t: t.family_id == token.family_id, now)
            return Reused(token.user_id, token.family_id)
        user = self._user(token.user_id)
        live = token.expires_at > now and token.family_expires_at > now
        if user is None or not user.is_active or not live:
            return NotFound()
        self.tokens[token.token_hash] = replace(found, revoked_at=now)
        expires_at = min(now + timedelta(seconds=refresh_ttl_seconds), token.family_expires_at)
        self._store(replace(token, token_hash=new_token_hash, expires_at=expires_at))
        return Rotated(Principal(user.user_id, user.team_id, user.role), token.family_id)

    async def revoke_user(self, user_id: uuid.UUID, *, now: datetime) -> None:
        self._revoke(lambda t: t.user_id == user_id, now)

    def _revoke(self, matches: Callable[[NewRefreshToken], bool], now: datetime) -> None:
        for key, stored in self.tokens.items():
            if matches(stored.token) and stored.revoked_at is None:
                self.tokens[key] = replace(stored, revoked_at=now)

    def _user(self, user_id: uuid.UUID) -> _User | None:
        return next((u for u in self._users.users.values() if u.user_id == user_id), None)


class FakeRateLimiter:
    """The same GCRA as the Lua script, in memory. `down=True` simulates Redis down."""

    def __init__(self) -> None:
        self._tat: dict[str, float] = {}
        self.down = False

    async def acquire(
        self, key: str, *, limit: int, period_seconds: int, now: datetime, cost: int = 1
    ) -> RateDecision:
        if self.down:
            raise DependencyUnavailableError("redis")
        now_ms = math.floor(now.timestamp() * 1000)
        interval = gcra_interval_ms(limit, period_seconds)
        tat = max(self._tat.get(key, now_ms), now_ms)
        new_tat = tat + interval * max(cost, 1)
        allow_at = new_tat - period_seconds * 1000
        if allow_at > now_ms:
            return RateDecision(False, math.floor(allow_at - now_ms) / 1000)
        if cost > 0:
            self._tat[key] = new_tat
        return RateDecision(True, 0.0)


class FakeRevocationPublisher:
    def __init__(self) -> None:
        self.published: list[Revocation] = []

    async def publish(self, revocation: Revocation) -> None:
        self.published.append(revocation)
