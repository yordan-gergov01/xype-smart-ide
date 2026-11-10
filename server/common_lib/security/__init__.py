"""Security: payload encryption, key handling, obfuscated ids, scrubbing and the
`KeyStore` seam (ADR-0015, ADR-0016, ADR-0019). No I/O except reading the scrub rules
file; the Redis adapter lives in `common_lib.keystore`."""

from common_lib.security.crypto_box import (
    MAX_ENCRYPTIONS_PER_DIRECTION,
    CryptoBox,
    EncryptedPayload,
    PayloadContext,
    counter_of,
    nonce_for,
)
from common_lib.security.errors import (
    DecryptionError,
    DuplicateKeyError,
    KeyBoundElsewhereError,
    KeyExchangeError,
    KeyNotFoundError,
    NonceError,
    ScrubTimeoutError,
    SecurityError,
)
from common_lib.security.framing import frame
from common_lib.security.keys import (
    Direction,
    ExchangeResult,
    Kek,
    KekPurpose,
    KekRing,
    WrappedKey,
    direction_key,
    hkdf,
    new_key,
    server_exchange,
    unwrap_key,
    wrap_key,
)
from common_lib.security.keystore import SESSION_KEY_TTL_SECONDS, KeyStore, SessionKeyRecord
from common_lib.security.obfuscation import IdDomain, hmac_id
from common_lib.security.scrubber import ScrubResult, ScrubRules, SecretScrubber, load_rules

__all__ = [
    "MAX_ENCRYPTIONS_PER_DIRECTION",
    "SESSION_KEY_TTL_SECONDS",
    "CryptoBox",
    "DecryptionError",
    "Direction",
    "DuplicateKeyError",
    "EncryptedPayload",
    "ExchangeResult",
    "IdDomain",
    "Kek",
    "KekPurpose",
    "KekRing",
    "KeyBoundElsewhereError",
    "KeyExchangeError",
    "KeyNotFoundError",
    "KeyStore",
    "NonceError",
    "PayloadContext",
    "ScrubResult",
    "ScrubRules",
    "ScrubTimeoutError",
    "SecretScrubber",
    "SecurityError",
    "SessionKeyRecord",
    "WrappedKey",
    "counter_of",
    "direction_key",
    "frame",
    "hkdf",
    "hmac_id",
    "load_rules",
    "new_key",
    "nonce_for",
    "server_exchange",
    "unwrap_key",
    "wrap_key",
]
