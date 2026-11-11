"""`FakeKeyStore`: the in-memory `KeyStore`, with expiry driven by a `Clock`.

It keeps the key bytes as given (no KEK); what the real adapter stores is checked by
its own integration test.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from common_lib.clock import Clock
from common_lib.errors import InvalidValueError
from common_lib.secret_bytes import SecretBytes
from common_lib.security import (
    REVOCATION_MEMORY_SECONDS,
    Direction,
    DuplicateKeyError,
    KeyBoundElsewhereError,
    KeyNotFoundError,
    KeyRevokedError,
    NonceError,
    SessionKeyRecord,
)


@dataclass
class _Entry:
    record: SessionKeyRecord
    key: bytes
    expires_at_s: float
    session_id: str | None = None
    received: int = 0
    sent: int = 0


class FakeKeyStore:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._entries: dict[str, _Entry] = {}
        # Tombstones: id -> (revoked at, remembered until).
        self._revoked_users: dict[str, tuple[datetime, datetime]] = {}
        self._revoked_families: dict[str, datetime] = {}

    async def put(
        self,
        record: SessionKeyRecord,
        key: SecretBytes,
        *,
        ttl_seconds: int,
        max_active: int,
        credentials_issued_at: datetime,
    ) -> None:
        if ttl_seconds <= 0 or max_active <= 0:
            raise InvalidValueError("a session key needs a positive TTL and cap")
        now = self._clock.now()
        family_until = self._revoked_families.get(record.family_id)
        revoked_at, user_until = self._revoked_users.get(record.user_id, (now, now))
        if (family_until is not None and family_until > now) or (
            user_until > now and credentials_issued_at <= revoked_at
        ):
            raise KeyRevokedError("the user or token family was revoked")
        if self._live_or_none(record.key_id) is not None:
            raise DuplicateKeyError("a session key with this id already exists")
        expires = (now + timedelta(seconds=ttl_seconds)).timestamp()
        self._entries[record.key_id] = _Entry(record, key.reveal(), expires)
        others = sorted(
            (e.record.created_at, e.record.key_id)
            for e in self._entries.values()
            if e.record.user_id == record.user_id
            and e.record.key_id != record.key_id
            and self._live_or_none(e.record.key_id) is not None
        )
        for _, key_id in others[: max(0, len(others) + 1 - max_active)]:
            del self._entries[key_id]

    async def get(self, key_id: str) -> tuple[SessionKeyRecord, SecretBytes]:
        entry = self._live(key_id)
        return entry.record, SecretBytes(entry.key)

    async def bind_stream(self, key_id: str, session_id: str) -> None:
        entry = self._live(key_id)
        if entry.session_id is None:
            entry.session_id = session_id
        elif entry.session_id != session_id:
            raise KeyBoundElsewhereError("the session key belongs to another stream")

    async def advance_nonce(self, key_id: str, direction: Direction, counter: int) -> None:
        if direction is not Direction.CLIENT_TO_SERVER:
            raise NonceError("the server only receives client-to-server payloads")
        entry = self._live(key_id)
        if counter <= entry.received:
            raise NonceError("the nonce counter was not above the highest seen")
        entry.received = counter

    async def next_counter(self, key_id: str, direction: Direction) -> int:
        if direction is not Direction.SERVER_TO_CLIENT:
            raise NonceError("the server only sends server-to-client payloads")
        entry = self._live(key_id)
        entry.sent += 1
        return entry.sent

    async def revoke_user(self, user_id: str, *, refuse_issued_until: datetime) -> None:
        self._revoked_users[user_id] = (refuse_issued_until, self._remembered_until())
        self._entries = {k: e for k, e in self._entries.items() if e.record.user_id != user_id}

    async def revoke_family(self, family_id: str) -> None:
        self._revoked_families[family_id] = self._remembered_until()
        self._entries = {k: e for k, e in self._entries.items() if e.record.family_id != family_id}

    def holds(self, key_id: str) -> bytes | None:
        """The stored key bytes (tests compare them with what the client unwrapped)."""
        entry = self._live_or_none(key_id)
        return None if entry is None else entry.key

    def _remembered_until(self) -> datetime:
        return self._clock.now() + timedelta(seconds=REVOCATION_MEMORY_SECONDS)

    def _live_or_none(self, key_id: str) -> _Entry | None:
        entry = self._entries.get(key_id)
        if entry is None or entry.expires_at_s <= self._clock.now().timestamp():
            return None
        return entry

    def _live(self, key_id: str) -> _Entry:
        entry = self._live_or_none(key_id)
        if entry is None:
            raise KeyNotFoundError("the session key is unknown or expired")
        return entry
