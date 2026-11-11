"""Short-lived access tokens: JWTs signed with Ed25519 (ADR-0014).

Verification trusts nothing in the token header but `kid`, which is looked up in the
local key set only: `jku`, `x5u` and `jwk` are never fetched or used, the algorithm is
pinned to EdDSA, and `none` or HMAC tokens are refused.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from common_lib.auth import ROLES, AccessClaims, InvalidTokenError, Principal
from common_lib.clock import Clock
from common_lib.errors import InvalidValueError
from common_lib.secret_bytes import SecretBytes

ALGORITHM = "EdDSA"
AUDIENCE = "xype-gateway"
CLOCK_SKEW_SECONDS = 30
REQUIRED_CLAIMS = ("iss", "aud", "sub", "team", "role", "fam", "iat", "exp", "jti")


@dataclass(frozen=True, slots=True)
class SigningKey:
    key_id: str
    signer: Ed25519PrivateKey

    @classmethod
    def from_seed(cls, key_id: str, seed: SecretBytes) -> "SigningKey":
        """An Ed25519 key from its 32-byte seed (`AUTH_SIGNING_KEY_*`)."""
        if not key_id:
            raise InvalidValueError("signing key id must be non-empty")
        return cls(key_id, Ed25519PrivateKey.from_private_bytes(seed.reveal()))


class AccessTokenIssuer:
    """Issues with the current key; verifies with the current and the previous one."""

    def __init__(
        self,
        current: SigningKey,
        previous: SigningKey | None = None,
        *,
        issuer: str,
        ttl_seconds: int,
        clock: Clock,
    ) -> None:
        if previous is not None and previous.key_id == current.key_id:
            raise InvalidValueError("the previous signing key needs its own id")
        self._current = current
        self._public: dict[str, Ed25519PublicKey] = {
            k.key_id: k.signer.public_key() for k in (current, previous) if k is not None
        }
        self._issuer = issuer
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock

    @property
    def ttl_seconds(self) -> int:
        return int(self._ttl.total_seconds())

    def issue(self, principal: Principal, family_id: uuid.UUID) -> str:
        now = self._clock.now()
        claims = {
            "iss": self._issuer,
            "aud": AUDIENCE,
            "sub": str(principal.user_id),
            "team": str(principal.team_id),
            "role": principal.role,
            "fam": str(family_id),
            "iat": int(now.timestamp()),
            "exp": int((now + self._ttl).timestamp()),
            "jti": uuid.uuid4().hex,
        }
        return jwt.encode(
            claims,
            self._current.signer,
            algorithm=ALGORITHM,
            headers={"kid": self._current.key_id},
        )

    def verify(self, token: str) -> AccessClaims:
        """Raises `InvalidTokenError` for anything but a valid, current token of ours."""
        try:
            header = jwt.get_unverified_header(token)
            key = self._public.get(str(header.get("kid")))
            if key is None or header.get("alg") != ALGORITHM:
                raise InvalidTokenError
            claims = jwt.decode(
                token,
                key,
                algorithms=[ALGORITHM],
                audience=AUDIENCE,
                issuer=self._issuer,
                # Times are checked below against the injected clock, like issuing.
                options={
                    "require": list(REQUIRED_CLAIMS),
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                },
            )
            access = _claims(claims)
            self._check_times(access.issued_at, access.expires_at)
            return access
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            raise InvalidTokenError from None

    def _check_times(self, issued_at: datetime, expires_at: datetime) -> None:
        now = self._clock.now()
        skew = timedelta(seconds=CLOCK_SKEW_SECONDS)
        if expires_at + skew <= now or issued_at - skew > now:
            raise InvalidTokenError


def _claims(claims: dict[str, object]) -> AccessClaims:
    """Raises KeyError or ValueError for a claim of the wrong shape."""
    principal = Principal(
        user_id=uuid.UUID(str(claims["sub"])),
        team_id=uuid.UUID(str(claims["team"])),
        role=ROLES[str(claims["role"])],
    )
    expires_at = datetime.fromtimestamp(int(str(claims["exp"])), tz=UTC)
    issued_at = datetime.fromtimestamp(int(str(claims["iat"])), tz=UTC)
    return AccessClaims(principal, uuid.UUID(str(claims["fam"])), expires_at, issued_at)
