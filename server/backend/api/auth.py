"""`/api/v1/auth`: log in, refresh, and who am I (ADR-0014).

Bodies are validated here and never logged; secrets and tokens are `SecretStr`, so
they cannot appear in a repr either.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from backend.api.dependencies.auth import get_claims
from backend.api.dependencies.services import get_auth_service
from backend.api.errors import NO_STORE
from backend.services.auth import AuthService
from common_lib.auth import AccessClaims, Credential, Role, TokenPair
from common_lib.secret_bytes import SecretBytes

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# The shape from backend.services.auth.peppers: anything else is refused before a lookup.
CLIENT_ID = r"^xc_[0-9a-f]{16}$"


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
