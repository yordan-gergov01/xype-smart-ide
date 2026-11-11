"""Peppered HMACs of client secrets and refresh tokens, and fresh random values
(ADR-0014). A 256-bit random value needs no slow hash; the pepper keeps a database
dump from being checked offline."""

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from common_lib.errors import InvalidValueError
from common_lib.secret_bytes import SecretBytes

CLIENT_ID_PREFIX = "xc_"
# Prefixes, not values: they let people and secret scanners recognise a leaked value.
CLIENT_SECRET_PREFIX = "xcs_"  # noqa: S105
REFRESH_TOKEN_PREFIX = "xrt_"  # noqa: S105
# token_urlsafe(32): 256 bits as 43 base64url characters.
RANDOM_BYTES = 32
_SEPARATOR = "$"


@dataclass(frozen=True, slots=True)
class Pepper:
    pepper_id: str
    key: SecretBytes

    def __post_init__(self) -> None:
        if not self.pepper_id or _SEPARATOR in self.pepper_id:
            raise InvalidValueError("pepper id must be non-empty and contain no '$'")

    def digest(self, value: bytes) -> str:
        return hmac.new(self.key.reveal(), value, hashlib.sha256).hexdigest()


class PepperRing:
    """The current pepper hashes; the previous one still verifies during a rotation."""

    def __init__(self, current: Pepper, previous: Pepper | None = None) -> None:
        if previous is not None and previous.pepper_id == current.pepper_id:
            raise InvalidValueError("the previous pepper needs its own id")
        self._current = current
        self._peppers = {p.pepper_id: p for p in (current, previous) if p is not None}

    def secret_hash(self, secret: bytes) -> str:
        """`<pepper id>$<hex>` under the current pepper, as stored in `users`."""
        return f"{self._current.pepper_id}{_SEPARATOR}{self._current.digest(secret)}"

    def secret_matches(self, secret: bytes, stored: str | None) -> bool:
        """Constant time in the secret. `stored=None` (an unknown client id) still does
        the HMAC and the comparison, so timing does not reveal which ids exist."""
        pepper_id, _, expected = (stored or "").partition(_SEPARATOR)
        pepper = self._peppers.get(pepper_id)
        actual = (pepper or self._current).digest(secret)
        if pepper is None:
            # Unknown client id or a retired pepper: the same work, never a match.
            hmac.compare_digest(actual, actual)
            return False
        return hmac.compare_digest(actual, expected)

    def token_hash(self, token: bytes) -> str:
        """The hex HMAC under the current pepper, as stored in `refresh_tokens`."""
        return self._current.digest(token)

    def token_hashes(self, token: bytes) -> list[str]:
        """Under every pepper, current first: a token issued before a rotation matches."""
        return [p.digest(token) for p in self._peppers.values()]


def new_client_id() -> str:
    return CLIENT_ID_PREFIX + secrets.token_hex(8)


def new_client_secret() -> str:
    return CLIENT_SECRET_PREFIX + secrets.token_urlsafe(RANDOM_BYTES)


def new_refresh_token() -> str:
    return REFRESH_TOKEN_PREFIX + secrets.token_urlsafe(RANDOM_BYTES)
