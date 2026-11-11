"""Security errors. Messages are fixed strings: never a key, nonce or payload byte."""

from common_lib.errors import XypeError


class SecurityError(XypeError):
    """Root of the security module's errors."""


class DecryptionError(SecurityError):
    """A payload or wrapped key failed authentication (wrong key, AAD or tampering)."""


class KeyExchangeError(SecurityError):
    """The client's public key was malformed or gave an all-zero shared secret."""


class NonceError(SecurityError):
    """A nonce counter was not above the highest one seen, or a key is used up."""


class KeyNotFoundError(SecurityError):
    """The session key does not exist, expired or was revoked."""


class KeyBoundElsewhereError(SecurityError):
    """The session key is bound to another stream (ADR-0015, one key per stream)."""


class DuplicateKeyError(SecurityError):
    """A session key with this id already exists; keys are never overwritten."""


class ScrubTimeoutError(SecurityError):
    """Scrubbing took longer than its budget; the text is treated as not sendable."""
