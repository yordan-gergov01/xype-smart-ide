"""`/api/v1/auth`: log in, refresh, who am I (ADR-0014) and session keys with the
requested repositories' HMAC keys (ADR-0015, ADR-0016).

Bodies are validated here and never logged; secrets and tokens are `SecretStr`, so
they cannot appear in a repr either.
"""

import base64
import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from backend.api.dependencies.auth import get_claims
from backend.api.dependencies.services import get_auth_service, get_session_key_service
from backend.api.errors import NO_STORE
from backend.services.auth import AuthService, SessionKeyService
from common_lib.auth import AccessClaims, Credential, Role, TokenPair
from common_lib.secret_bytes import SecretBytes

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# The shape from backend.services.auth.peppers: anything else is refused before a lookup.
CLIENT_ID = r"^xc_[0-9a-f]{16}$"
# A raw 32-byte X25519 public key, standard base64 with padding.
PUBLIC_KEY = r"^[A-Za-z0-9+/]{42}[AEIMQUYcgkosw048]=$"
# The repositories one IDE window may open at once.
MAX_REPOSITORIES_PER_KEY = 64


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TokenRequest(_Strict):
    client_id: str = Field(pattern=CLIENT_ID)
    client_secret: SecretStr = Field(min_length=4, max_length=80)


class RefreshRequest(_Strict):
    refresh_token: SecretStr = Field(min_length=4, max_length=80)


class TokenResponse(_Strict):
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: Literal["Bearer"] = "Bearer"  # noqa: S105 - the OAuth token type, not a value


class SessionKeyRequest(_Strict):
    client_public_key: str = Field(pattern=PUBLIC_KEY)
    repository_ids: list[uuid.UUID] = Field(
        default_factory=list, max_length=MAX_REPOSITORIES_PER_KEY
    )


class RepositoryKeyResponse(_Strict):
    """`wrapped_key` is `nonce(12) ‖ ciphertext ‖ tag(16)` under the exchange's wrapping
    key, with AAD `"repo-hmac" ‖ repository_id ‖ hmac_key_id` (`seal_repository_key`)."""

    repository_id: uuid.UUID
    hmac_key_id: str
    wrapped_key: str


class SessionKeyResponse(_Strict):
    """Bytes as standard base64; `wrapped_key` is `nonce(12) ‖ ciphertext ‖ tag(16)`.
    `repository_keys` holds one entry per requested repository the team may access."""

    key_id: str
    server_public_key: str
    wrapped_key: str
    expires_at: datetime
    repository_keys: list[RepositoryKeyResponse]


class MeResponse(_Strict):
    user_id: str
    team_id: str
    role: Role


def _response(pair: TokenPair, response: Response) -> TokenResponse:
    response.headers.update(NO_STORE)
    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in_seconds,
    )


@router.post("/token", response_model=TokenResponse)
async def token(
    body: TokenRequest,
    response: Response,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> TokenResponse:
    """Client credentials in, a new token family out (one per IDE window)."""
    secret = SecretBytes(body.client_secret.get_secret_value().encode())
    try:
        pair = await service.login(Credential("client_credentials", body.client_id, secret))
    finally:
        secret.wipe()
    return _response(pair, response)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    response: Response,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> TokenResponse:
    """Rotates the refresh token; reusing an old one revokes its family."""
    return _response(await service.refresh(body.refresh_token.get_secret_value()), response)


@router.get("/me", response_model=MeResponse)
async def me(claims: Annotated[AccessClaims, Depends(get_claims)]) -> MeResponse:
    """The authenticated principal; lets the extension check its credentials."""
    principal = claims.principal
    return MeResponse(
        user_id=str(principal.user_id), team_id=str(principal.team_id), role=principal.role
    )


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


@router.post("/session-key", response_model=SessionKeyResponse)
async def session_key(
    body: SessionKeyRequest,
    response: Response,
    claims: Annotated[AccessClaims, Depends(get_claims)],
    service: Annotated[SessionKeyService, Depends(get_session_key_service)],
) -> SessionKeyResponse:
    """A new session key for one stream, and the requested repositories' HMAC keys,
    wrapped for this client's ephemeral key."""
    client_key = base64.b64decode(body.client_public_key, validate=True)
    minted = await service.mint(claims, client_key, body.repository_ids)
    response.headers.update(NO_STORE)
    return SessionKeyResponse(
        key_id=minted.key_id,
        server_public_key=_b64(minted.server_public_key),
        wrapped_key=_b64(minted.wrapped_key),
        expires_at=minted.expires_at,
        repository_keys=[
            RepositoryKeyResponse(
                repository_id=key.repository_id,
                hmac_key_id=key.hmac_key_id,
                wrapped_key=_b64(key.wrapped_key),
            )
            for key in minted.repository_keys
        ],
    )
