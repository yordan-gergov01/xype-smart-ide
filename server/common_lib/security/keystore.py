"""The `KeyStore` seam: session keys wrapped under the KEK, with their nonce counters,
stream binding and revocation (ADR-0015). Adapters: `common_lib.keystore.RedisKeyStore`
(the non-evicting `keystore` Redis) and the in-memory fake in the tests.

The server receives only client-to-server payloads and sends only server-to-client
ones, so `advance_nonce` accepts only the first direction and `next_counter` only the
second; any other use raises `NonceError` (a nonce reused across sides breaks GCM).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from common_lib.secret_bytes import SecretBytes
from common_lib.security.errors import KeyNotFoundError
from common_lib.security.keys import Direction

# A working day plus queued jobs that outlive the stream (ADR-0015).
SESSION_KEY_TTL_SECONDS = 24 * 60 * 60
# A stream may use a key for 12 hours after it was minted; the store keeps it longer only
# so the indexer can still finish queued jobs (ADR-0015).
SESSION_KEY_MAX_AGE_SECONDS = 12 * 60 * 60
# How long a revocation refuses new keys: the longest access-token lifetime (15 min),
# twice its 30 s clock skew (the admin offsets the user tombstone by one, ADR-0015) and
# a minute of headroom (ADR-0014).
REVOCATION_MEMORY_SECONDS = 17 * 60


@dataclass(frozen=True, slots=True)
class SessionKeyRecord:
    """Who a session key belongs to; never the key itself."""

    key_id: str
    user_id: str
    family_id: str
    created_at: datetime


def check_key_age(record: SessionKeyRecord, now: datetime) -> None:
    """`KeyNotFoundError` once a key is past its maximum age for streams (ADR-0015)."""
    if now - record.created_at >= timedelta(seconds=SESSION_KEY_MAX_AGE_SECONDS):
        raise KeyNotFoundError("the session key is past its maximum age")


class KeyStore(Protocol):
    async def put(
        self,
        record: SessionKeyRecord,
        key: SecretBytes,
        *,
        ttl_seconds: int,
        max_active: int,
        credentials_issued_at: datetime,
    ) -> None:
        """Stores a new key wrapped. The caller still wipes `key`.

        - `DuplicateKeyError` if the id exists (a key is never overwritten, its counters
          would restart).
        - `KeyRevokedError` if the record's family was revoked, or its user was revoked
          at or after `credentials_issued_at` (the access token's `iat`), within the last
          `REVOCATION_MEMORY_SECONDS`: a token that outlives a revocation mints nothing.
        - Afterwards the user holds at most `max_active` keys: the oldest others (by
          `created_at`) are deleted with their bindings and counters.
        """

    async def get(self, key_id: str) -> tuple[SessionKeyRecord, SecretBytes]:
        """The record and the unwrapped key (caller wipes); `KeyNotFoundError` if absent."""

    async def bind_stream(self, key_id: str, session_id: str) -> None:
        """Binds the key to one stream; `KeyBoundElsewhereError` for any other stream."""

    async def advance_nonce(self, key_id: str, direction: Direction, counter: int) -> None:
        """Records a received counter; `NonceError` unless it is above the highest seen.

        Call it only after `CryptoBox.open` succeeded: an unauthenticated high counter
        would otherwise use up the replay window and block the real stream."""

    async def next_counter(self, key_id: str, direction: Direction) -> int:
        """Atomically allocates the next counter for a payload this server sends."""

    async def revoke_user(self, user_id: str, *, refuse_issued_until: datetime) -> None:
        """Deletes every key of the user (disabled, secret rotated), with bindings and
        counters, and refuses keys for credentials issued up to `refuse_issued_until`
        (see `put`). Not the plain clock: pass the revocation time plus the clock skew,
        so tokens signed while the revocation ran are refused too."""

    async def revoke_family(self, family_id: str) -> None:
        """Deletes the keys of one token family (refresh-token reuse, ADR-0014) and
        refuses new keys for it (see `put`)."""
