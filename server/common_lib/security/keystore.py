"""The `KeyStore` seam: session keys wrapped under the KEK, with their nonce counters,
stream binding and revocation (ADR-0015). Adapters: `common_lib.keystore.RedisKeyStore`
(the non-evicting `keystore` Redis) and the in-memory fake in the tests.

The server receives only client-to-server payloads and sends only server-to-client
ones, so `advance_nonce` accepts only the first direction and `next_counter` only the
second; any other use raises `NonceError` (a nonce reused across sides breaks GCM).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from common_lib.secret_bytes import SecretBytes
from common_lib.security.keys import Direction

# A working day plus queued jobs that outlive the stream (ADR-0015).
SESSION_KEY_TTL_SECONDS = 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class SessionKeyRecord:
    """Who a session key belongs to; never the key itself."""

    key_id: str
    user_id: str
    family_id: str
    created_at: datetime


class KeyStore(Protocol):
    async def put(self, record: SessionKeyRecord, key: SecretBytes, *, ttl_seconds: int) -> None:
        """Stores a new key wrapped; `DuplicateKeyError` if the id exists (a key is never
        overwritten, its counters would restart). The caller still wipes `key`."""

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

    async def revoke_user(self, user_id: str) -> None:
        """Deletes every key of the user (user disabled), with bindings and counters."""

    async def revoke_family(self, family_id: str) -> None:
        """Deletes the keys of one token family (refresh-token reuse, ADR-0014)."""
