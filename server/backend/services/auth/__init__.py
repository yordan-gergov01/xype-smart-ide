"""Auth: client credentials, token families, access tokens and rate limits (ADR-0014)."""

from backend.services.auth.access_tokens import AccessTokenIssuer, SigningKey
from backend.services.auth.admin import ClientAdmin, UnknownClientError
from backend.services.auth.peppers import Pepper, PepperRing
from backend.services.auth.ports import (
    CredentialVerifier,
    RateLimiter,
    RefreshTokenStore,
    RevocationPublisher,
    UserStore,
)
from backend.services.auth.service import AuthDependencies, AuthPolicy, AuthService
from backend.services.auth.session_keys import (
    MintedSessionKey,
    SessionKeyPolicy,
    SessionKeyService,
)
from backend.services.auth.verifier import ClientCredentialVerifier
from common_lib.auth import (
    ROLES,
    AccessClaims,
    AuthError,
    ConflictError,
    Credential,
    InvalidCredentialsError,
    InvalidTokenError,
    NewClient,
    NewRefreshToken,
    NotFound,
    Principal,
    RateDecision,
    RateLimitedError,
    Reused,
    Revocation,
    Role,
    Rotated,
    Rotation,
    StoredCredentials,
    TokenPair,
    VerifiedCredential,
)

__all__ = [
    "ROLES",
    "AccessClaims",
    "AccessTokenIssuer",
    "AuthDependencies",
    "AuthError",
    "AuthPolicy",
    "AuthService",
    "ClientAdmin",
    "ClientCredentialVerifier",
    "ConflictError",
    "Credential",
    "CredentialVerifier",
    "InvalidCredentialsError",
    "InvalidTokenError",
    "MintedSessionKey",
    "NewClient",
    "NewRefreshToken",
    "NotFound",
    "Pepper",
    "PepperRing",
    "Principal",
    "RateDecision",
    "RateLimitedError",
    "RateLimiter",
    "RefreshTokenStore",
    "Reused",
    "Revocation",
    "RevocationPublisher",
    "Role",
    "Rotated",
    "Rotation",
    "SessionKeyPolicy",
    "SessionKeyService",
    "SigningKey",
    "StoredCredentials",
    "TokenPair",
    "UnknownClientError",
    "UserStore",
    "VerifiedCredential",
]
