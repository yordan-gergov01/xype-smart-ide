"""The bearer-token dependency of every authenticated route: verifies the access token
and counts the request against the user's rate limit (ADR-0014)."""

from typing import Annotated

from fastapi import Depends, Request

from backend.api.dependencies.services import get_auth_service
from backend.services.auth import AuthService
from common_lib.auth import AccessClaims, InvalidTokenError

_SCHEME = "bearer "


async def get_claims(
    request: Request, service: Annotated[AuthService, Depends(get_auth_service)]
) -> AccessClaims:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith(_SCHEME):
        raise InvalidTokenError
    return await service.authenticate(header[len(_SCHEME) :].strip())
