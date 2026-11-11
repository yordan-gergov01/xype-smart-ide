"""Domain errors to HTTP responses. Every body is a fixed code: no request content,
exception message or rejected value is ever sent back (rule 08).

FastAPI's default 422 body repeats the rejected input, which for `/auth/token` would
be the client secret; it is replaced here for every route.
"""

import math

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from common_lib.auth import InvalidCredentialsError, InvalidTokenError, RateLimitedError
from common_lib.errors import DependencyUnavailableError

NO_STORE = {"Cache-Control": "no-store"}


def error(code: str, status_code: int, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status_code, headers=headers)


async def _invalid_request(_: Request, __: Exception) -> JSONResponse:
    return error("invalid_request", status.HTTP_422_UNPROCESSABLE_CONTENT)


async def _invalid_credentials(_: Request, __: Exception) -> JSONResponse:
    headers = {"WWW-Authenticate": 'Bearer error="invalid_client"', **NO_STORE}
    return error("invalid_credentials", status.HTTP_401_UNAUTHORIZED, headers)


async def _invalid_token(_: Request, __: Exception) -> JSONResponse:
    headers = {"WWW-Authenticate": 'Bearer error="invalid_token"', **NO_STORE}
    return error("invalid_token", status.HTTP_401_UNAUTHORIZED, headers)


async def _rate_limited(_: Request, exc: Exception) -> JSONResponse:
    retry_after = exc.retry_after_seconds if isinstance(exc, RateLimitedError) else 1.0
    headers = {"Retry-After": str(max(1, math.ceil(retry_after)))}
    return error("rate_limited", status.HTTP_429_TOO_MANY_REQUESTS, headers)


async def _unavailable(_: Request, __: Exception) -> JSONResponse:
    # Fail closed: without its stores the gateway refuses, it never lets a request through.
    return error("unavailable", status.HTTP_503_SERVICE_UNAVAILABLE)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, _invalid_request)
    app.add_exception_handler(InvalidCredentialsError, _invalid_credentials)
    app.add_exception_handler(InvalidTokenError, _invalid_token)
    app.add_exception_handler(RateLimitedError, _rate_limited)
    app.add_exception_handler(DependencyUnavailableError, _unavailable)
