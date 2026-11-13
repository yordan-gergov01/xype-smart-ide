"""Payload encryption: AES-256-GCM with per-direction keys and counter nonces (ADR-0015).

`CryptoBox` is a concrete class, not a seam: it has one implementation and no external
dependency, like `hmac_id`. The nonce is 4 zero bytes and a 64-bit big-endian counter;
the caller owns the counters (`KeyStore.next_counter` and `advance_nonce`), this class
only refuses counters outside the key's budget.
"""

from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from common_lib.errors import InvalidValueError
from common_lib.secret_bytes import SecretBytes
from common_lib.security.errors import DecryptionError, NonceError
from common_lib.security.framing import frame
from common_lib.security.keys import Direction, direction_key

AAD_VERSION = "2"
# Far below GCM's 2^32 limit for random-looking nonces; the server enforces it.
MAX_ENCRYPTIONS_PER_DIRECTION = 2**24
_NONCE_PREFIX = bytes(4)
NONCE_BYTES = 12


@dataclass(frozen=True, slots=True)
class PayloadContext:
    """What a payload is bound to. Fields a message has no use for stay empty."""

    key_id: str
    direction: Direction
    message_id: str
    request_id: str = ""
    field_number: int = 0
    repository_id: str = ""
    obfuscated_file_id: str = ""
    chunk_hash: str = ""
    line_start: int = 0
    line_end: int = 0
    leaf_node_id: str = ""

    def __post_init__(self) -> None:
        if bool(self.chunk_hash) != bool(self.leaf_node_id):
            # A chunk payload without its leaf id would silently lose the leaf binding.
            raise InvalidValueError("a chunk context needs both chunk_hash and leaf_node_id")

    def aad(self) -> bytes:
        """The canonical AAD; the receiver rebuilds it, it never trusts sent bytes."""
        return frame(
            AAD_VERSION,
            str(self.direction),
            self.key_id,
            self.message_id,
            self.request_id,
            self.field_number,
            self.repository_id,
            self.obfuscated_file_id,
            self.chunk_hash,
            self.line_start,
            self.line_end,
            self.leaf_node_id,
        )


@dataclass(frozen=True, slots=True)
class EncryptedPayload:
    """Mirrors `xype.v1.EncryptedPayload`; `ciphertext` includes the 16-byte tag."""

    key_id: str
    nonce: bytes
    ciphertext: bytes


def nonce_for(counter: int) -> bytes:
    if not 1 <= counter <= MAX_ENCRYPTIONS_PER_DIRECTION:
        raise NonceError("the nonce counter is outside the key's budget")
    return _NONCE_PREFIX + counter.to_bytes(8, "big")


def counter_of(nonce: bytes) -> int:
    """The counter inside a received nonce; refuses any other nonce shape."""
    if len(nonce) != NONCE_BYTES or nonce[:4] != _NONCE_PREFIX:
        raise NonceError("the nonce is not a counter nonce")
    counter = int.from_bytes(nonce[4:], "big")
    nonce_for(counter)  # range check
    return counter


class CryptoBox:
    """Seals and opens payloads under a session key, for one side of the stream.

    `sends` is the direction this side encrypts in: the server seals only
    server-to-client and opens only client-to-server payloads, so the two sides never
    use the same nonce under the same direction key.
    """

    def __init__(self, *, sends: Direction) -> None:
        self._sends = sends

    def seal(
        self,
        session_key: SecretBytes,
        counter: int,
        plaintext: SecretBytes,
        context: PayloadContext,
    ) -> EncryptedPayload:
        if context.direction is not self._sends:
            raise NonceError("this side does not encrypt in that direction")
        key = direction_key(session_key, context.direction)
        try:
            nonce = nonce_for(counter)
            sealed = AESGCM(key.reveal()).encrypt(nonce, plaintext.reveal(), context.aad())
            return EncryptedPayload(context.key_id, nonce, sealed)
        finally:
            key.wipe()

    def open(
        self, session_key: SecretBytes, payload: EncryptedPayload, context: PayloadContext
    ) -> SecretBytes:
        """Decrypts into `SecretBytes`; the caller wipes it right after use."""
        if context.direction is self._sends:
            raise DecryptionError("this side does not receive in that direction")
        if payload.key_id != context.key_id:
            raise DecryptionError("the payload names another key")
        counter_of(payload.nonce)
        key = direction_key(session_key, context.direction)
        try:
            aead = AESGCM(key.reveal())
            return SecretBytes(aead.decrypt(payload.nonce, payload.ciphertext, context.aad()))
        except InvalidTag:
            raise DecryptionError("the payload failed authentication") from None
        finally:
            key.wipe()
