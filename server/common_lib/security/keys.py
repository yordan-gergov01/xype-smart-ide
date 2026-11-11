"""Key handling for ADR-0015: the KEK ring and its subkeys, key wrapping, the X25519
session-key exchange and the per-direction payload keys.

Every derived or unwrapped key is returned as `SecretBytes`; callers wipe it when done.
`cryptography` copies key bytes internally, which cannot be zeroed (honest limits in
`common_lib/secret_bytes.py`).
"""

import os
from dataclasses import dataclass
from enum import StrEnum

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from common_lib.errors import InvalidValueError
from common_lib.secret_bytes import SecretBytes
from common_lib.security.errors import DecryptionError, KeyExchangeError
from common_lib.security.framing import frame

KEY_BYTES = 32
_WRAP_NONCE_BYTES = 12
_EXCHANGE_INFO = b"xype session-key v1"


class KekPurpose(StrEnum):
    """Session keys and repository keys are wrapped under different subkeys."""

    SESSION_KEYS = "session-keys"
    REPO_KEYS = "repo-keys"


class Direction(StrEnum):
    CLIENT_TO_SERVER = "c2s"
    SERVER_TO_CLIENT = "s2c"


@dataclass(frozen=True, slots=True)
class Kek:
    """One key-encryption key and its id; the id is stored next to what it wraps."""

    kek_id: str
    material: SecretBytes

    def __post_init__(self) -> None:
        if not self.kek_id or len(self.material) != KEY_BYTES:
            raise InvalidValueError("a KEK needs an id and 32 bytes")


@dataclass(frozen=True, slots=True)
class KekRing:
    """The current KEK wraps; the previous one still unwraps during a rotation."""

    current: Kek
    previous: Kek | None = None

    def __post_init__(self) -> None:
        if self.previous is not None and self.previous.kek_id == self.current.kek_id:
            raise InvalidValueError("the current and previous KEK need different ids")

    def by_id(self, kek_id: str) -> Kek:
        for kek in (self.current, self.previous):
            if kek is not None and kek.kek_id == kek_id:
                return kek
        raise DecryptionError("no KEK with this id")


def hkdf(key: SecretBytes, info: bytes, *, salt: bytes | None = None) -> SecretBytes:
    """HKDF-SHA256 to a 32-byte key."""
    derived = HKDF(algorithm=hashes.SHA256(), length=KEY_BYTES, salt=salt, info=info)
    return SecretBytes(derived.derive(key.reveal()))


def kek_subkey(kek: Kek, purpose: KekPurpose) -> SecretBytes:
    return hkdf(kek.material, frame("xype kek", str(purpose)))


def direction_key(session_key: SecretBytes, direction: Direction) -> SecretBytes:
    """Each direction encrypts only with its own key (no reflection)."""
    return hkdf(session_key, frame("xype payload", str(direction)))


@dataclass(frozen=True, slots=True)
class WrappedKey:
    kek_id: str
    nonce: bytes
    ciphertext: bytes


def wrap_key(kek: Kek, purpose: KekPurpose, key: SecretBytes, aad: bytes) -> WrappedKey:
    """AES-256-GCM under the purpose's subkey, with a random nonce."""
    subkey = kek_subkey(kek, purpose)
    try:
        nonce = os.urandom(_WRAP_NONCE_BYTES)
        sealed = AESGCM(subkey.reveal()).encrypt(nonce, key.reveal(), aad)
        return WrappedKey(kek.kek_id, nonce, sealed)
    finally:
        subkey.wipe()


def unwrap_key(ring: KekRing, purpose: KekPurpose, wrapped: WrappedKey, aad: bytes) -> SecretBytes:
    subkey = kek_subkey(ring.by_id(wrapped.kek_id), purpose)
    try:
        return SecretBytes(AESGCM(subkey.reveal()).decrypt(wrapped.nonce, wrapped.ciphertext, aad))
    except InvalidTag:
        raise DecryptionError("a wrapped key failed authentication") from None
    finally:
        subkey.wipe()


@dataclass(frozen=True, slots=True)
class ExchangeResult:
    """The server's half of a session-key exchange (ADR-0015)."""

    server_public_key: bytes
    wrapping_key: SecretBytes


def server_exchange(
    client_public_key: bytes,
    *,
    key_id: str,
    user_id: str,
    server_private: X25519PrivateKey | None = None,
) -> ExchangeResult:
    """Derives the key that wraps a new session key for this client.

    `server_private` is for tests only; production uses a fresh ephemeral key.
    """
    try:
        client = X25519PublicKey.from_public_bytes(client_public_key)
    except ValueError:
        raise KeyExchangeError("the client public key is malformed") from None
    private = server_private or X25519PrivateKey.generate()
    server_public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    try:
        shared = SecretBytes(private.exchange(client))
    except ValueError:  # an all-zero shared secret (small-order point)
        raise KeyExchangeError("the key exchange gave an all-zero secret") from None
    if shared.is_zeroed():
        shared.wipe()
        raise KeyExchangeError("the key exchange gave an all-zero secret")
    info = frame(_EXCHANGE_INFO, client_public_key, server_public, user_id)
    try:
        return ExchangeResult(server_public, hkdf(shared, info, salt=key_id.encode("utf-8")))
    finally:
        shared.wipe()


def seal_session_key(
    wrapping_key: SecretBytes, key_id: str, session_key: SecretBytes, *, nonce: bytes | None = None
) -> bytes:
    """`nonce ‖ AES-256-GCM(wrapping_key, session_key, aad = "session" ‖ key_id)`, the
    `wrapped_key` the client unwraps (ADR-0015). `nonce` is for test vectors only."""
    nonce = nonce or os.urandom(_WRAP_NONCE_BYTES)
    if len(nonce) != _WRAP_NONCE_BYTES:
        raise InvalidValueError("a wrap nonce has 12 bytes")
    sealed = AESGCM(wrapping_key.reveal()).encrypt(
        nonce, session_key.reveal(), frame("session", key_id)
    )
    return nonce + sealed


def new_key() -> SecretBytes:
    """A fresh random 256-bit key."""
    return SecretBytes(os.urandom(KEY_BYTES))
