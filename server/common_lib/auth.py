"""Auth values and errors shared by the auth service and its adapters (ADR-0014)."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from common_lib.errors import XypeError
from common_lib.secret_bytes import SecretBytes

type Role = Literal["developer", "admin"]
# Parses a stored or claimed role; a KeyError means it is not one.
ROLES: dict[str, Role] = {"developer": "developer", "admin": "admin"}


class AuthError(XypeError):
    """Base of the auth feature's errors. Messages are fixed; none repeats input."""


class InvalidCredentialsError(AuthError):
    """Unknown client id, wrong secret or disabled user: one error for all three."""

    def __init__(self) -> None:
        super().__init__("invalid credentials")


class InvalidTokenError(AuthError):
    """An access or refresh token that is malformed, forged, expired or revoked."""

    def __init__(self) -> None:
        super().__init__("invalid token")


class ConflictError(AuthError):
    """An admin action would duplicate a team name, email or client id."""


class RateLimitedError(AuthError):
    """Too many requests; the caller may retry after `retry_after_seconds`."""

    def __init__(self, retry_after_seconds: float) -> None:
        super().__init__("rate limited")
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is calling, as the gateway authorizes it."""

    user_id: uuid.UUID
    team_id: uuid.UUID
    role: Role


@dataclass(frozen=True, slots=True)
class Credential:
    """What a client presents to log in: client credentials now, OIDC ID tokens later."""

    kind: Literal["client_credentials"]
    client_id: str
    secret: SecretBytes


@dataclass(frozen=True, slots=True)
class AccessClaims:
    """A verified access token: the principal, its token family, when it was issued
    (whole seconds, as in the token) and when it expires."""

    principal: Principal
    family_id: uuid.UUID
    expires_at: datetime
    issued_at: datetime


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_in_seconds: int = 0


@dataclass(frozen=True, slots=True)
class VerifiedCredential:
    """A verified credential, and the stored state it was verified against: a login only
    starts a token family while that state is unchanged (no race with an admin's
    `rotate-secret` or `disable-client`)."""

    principal: Principal
    secret_hash: str


@dataclass(frozen=True, slots=True)
class StoredCredentials:
    """A user's credential row: the peppered secret hash, never the secret."""

    principal: Principal
    secret_hash: str
    is_active: bool


@dataclass(frozen=True, slots=True)
class NewRefreshToken:
    user_id: uuid.UUID
    family_id: uuid.UUID
    token_hash: str
    expires_at: datetime
    family_expires_at: datetime


@dataclass(frozen=True, slots=True)
class Rotated:
    """The presented refresh token was valid; it is revoked and a new one is stored."""

    principal: Principal
    family_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class Reused:
    """The presented refresh token was already revoked: its whole family is revoked now."""

    user_id: uuid.UUID
    family_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class NotFound:
    """Unknown, expired, of a disabled user, or of a family past its absolute limit."""


type Rotation = Rotated | Reused | NotFound


@dataclass(frozen=True, slots=True)
class RateDecision:
    allowed: bool
    retry_after_seconds: float


@dataclass(frozen=True, slots=True)
class Revocation:
    """Published to every gateway replica, which closes the matching streams (slice 6)."""

    kind: Literal["user", "family"]
    subject_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class NewClient:
    """A client created or re-keyed by an admin; the secret is shown once, never stored."""

    user_id: uuid.UUID
    client_id: str
    client_secret: str = field(repr=False)
