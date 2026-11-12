"""What a `Session` stream may do: open with an access token, say Hello with a session key
and repositories, then send messages within the rate limit (ADR-0013, ADR-0015).

Transport-free: `backend.grpc` maps these values and errors to protobuf and gRPC.
"""

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import structlog

from backend.services.auth import AuthService
from backend.services.session.ports import RepositoryAccess
from backend.services.session.timers import StreamPolicy
from common_lib.auth import AccessClaims
from common_lib.clock import Clock
from common_lib.domain import is_hmac_hex
from common_lib.errors import XypeError
from common_lib.security import (
    DecryptionError,
    KeyBoundElsewhereError,
    KeyNotFoundError,
    KeyStore,
    SessionKeyRecord,
    check_key_age,
)

logger = structlog.get_logger(__name__)


class SessionError(XypeError):
    """Root of the session module's errors."""


class RefusedError(SessionError):
    """The stream refuses a message. `reason` is a fixed string, safe to send back."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


MALFORMED = "malformed message"
HELLO_FIRST = "hello expected first"
HELLO_TWICE = "hello already received"
UNSUPPORTED = "message not supported yet"
NO_ACCESS = "repository not accessible"
KEY_UNUSABLE = "session key unknown, expired or not yours"
KEY_IN_USE = "session key belongs to another stream"


@dataclass(frozen=True, slots=True)
class HelloRequest:
    session_key_id: str
    repository_ids: tuple[str, ...]
    repository_roots: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class OpenSession:
    """A stream after a valid Hello: who, under which key, over which repositories."""

    session_id: str
    claims: AccessClaims
    key: SessionKeyRecord
    repository_ids: frozenset[uuid.UUID]


class SessionGate:
    def __init__(
        self,
        auth: AuthService,
        keys: KeyStore,
        repositories: RepositoryAccess,
        *,
        policy: StreamPolicy,
        clock: Clock,
    ) -> None:
        self._auth = auth
        self._keys = keys
        self._repositories = repositories
        self._policy = policy
        self._clock = clock

    @property
    def policy(self) -> StreamPolicy:
        return self._policy

    async def open(self, access_token: str) -> AccessClaims:
        """Verifies the token of the stream call and counts it against the rate limit."""
        return await self._auth.authenticate(access_token)

    async def hello(
        self, claims: AccessClaims, hello: HelloRequest, session_id: str
    ) -> OpenSession:
        """Checks the repositories and the session key, then binds the key to this stream.

        The key must belong to the caller's user and token family: a key minted in one
        IDE window is not usable from another, and revoking a family revokes its keys.
        """
        await self._auth.count(claims)
        repositories = await self._authorize(claims, hello)
        record = await self._key_record(claims, hello.session_key_id)
        try:
            await self._keys.bind_stream(record.key_id, session_id)
        except KeyNotFoundError:
            raise RefusedError(KEY_UNUSABLE) from None
        except KeyBoundElsewhereError:
            raise RefusedError(KEY_IN_USE) from None
        logger.info(
            "session.hello",
            user_id=str(claims.principal.user_id),
            session_id=session_id,
            key_id=record.key_id,
            repositories=len(repositories),
        )
        return OpenSession(session_id, claims, record, repositories)

    async def message(self, session: OpenSession, repository_ids: Iterable[str] = ()) -> None:
        """Every later message: the rate limit, the key's age, and that the repositories the
        transport extracted from it were authorized at Hello. Each slice that handles a
        message kind extracts every repository id it carries, nested `ChunkRef`s included."""
        await self._auth.count(session.claims)
        try:
            check_key_age(session.key, self._clock.now())
        except KeyNotFoundError:
            raise RefusedError(KEY_UNUSABLE) from None
        if not _parse_ids(repository_ids) <= session.repository_ids:
            raise RefusedError(NO_ACCESS)

    async def _authorize(self, claims: AccessClaims, hello: HelloRequest) -> frozenset[uuid.UUID]:
        if len(hello.repository_ids) > self._policy.max_repositories:
            raise RefusedError(MALFORMED)
        ids = _parse_ids(hello.repository_ids)
        if len(ids) != len(hello.repository_ids):
            raise RefusedError(MALFORMED)
        roots = hello.repository_roots
        if not set(roots) <= set(hello.repository_ids) or not all(
            is_hmac_hex(value) for value in roots.values()
        ):
            raise RefusedError(MALFORMED)
        if ids and await self._repositories.accessible(claims.principal.team_id, ids) != ids:
            logger.warning("session.repository.refused", user_id=str(claims.principal.user_id))
            raise RefusedError(NO_ACCESS)
        return ids

    async def _key_record(self, claims: AccessClaims, key_id: str) -> SessionKeyRecord:
        # Key ids are UUIDv7 (`SessionKeyService`); anything else is refused unread.
        try:
            uuid.UUID(key_id)
        except ValueError:
            raise RefusedError(MALFORMED) from None
        try:
            record, key = await self._keys.get(key_id)
        except (KeyNotFoundError, DecryptionError):
            raise RefusedError(KEY_UNUSABLE) from None
        # Only the record is needed here; the key itself is used by the indexer.
        key.wipe()
        mine = record.user_id == str(claims.principal.user_id) and record.family_id == str(
            claims.family_id
        )
        if not mine:
            raise RefusedError(KEY_UNUSABLE)
        try:
            check_key_age(record, self._clock.now())
        except KeyNotFoundError:
            raise RefusedError(KEY_UNUSABLE) from None
        return record


def _parse_ids(values: Iterable[str]) -> frozenset[uuid.UUID]:
    try:
        return frozenset(uuid.UUID(value) for value in values)
    except ValueError:
        raise RefusedError(MALFORMED) from None
