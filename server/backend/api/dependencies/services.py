"""FastAPI dependencies that hand routes their services.

Services are built once in the composition root (`backend.main`) and kept in the
app state; routes depend on these functions, and tests override them with fakes.
"""

from fastapi import Request

from backend.services.auth import AuthService
from backend.services.health import HealthService


def get_health_service(request: Request) -> HealthService:
    service: HealthService = request.app.state.health_service
    return service


def get_auth_service(request: Request) -> AuthService:
    service: AuthService = request.app.state.auth_service
    return service
