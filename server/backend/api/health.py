"""Liveness, readiness and metrics endpoints. Outside `/api/v1`, no authentication."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel

from backend.api.dependencies.services import get_health_service
from backend.services.health import DependencyStatus, HealthService

router = APIRouter(tags=["health"])


class LivenessResponse(BaseModel):
    status: Literal["ok"]


class ReadinessResponse(BaseModel):
    status: Literal["ready", "unavailable"]
    dependencies: dict[str, DependencyStatus]


@router.get("/healthz", response_model=LivenessResponse)
async def healthz() -> LivenessResponse:
    """200 while the process serves requests; used by the container healthcheck."""
    return LivenessResponse(status="ok")


@router.get(
    "/readyz",
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def readyz(
    response: Response,
    service: Annotated[HealthService, Depends(get_health_service)],
) -> ReadinessResponse:
    """200 when every dependency answers, 503 otherwise, with each one's status."""
    readiness = await service.readiness()
    if not readiness.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ready" if readiness.ready else "unavailable",
        dependencies=readiness.dependencies,
    )


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    """Prometheus metrics of this process."""
    return Response(
        generate_latest(request.app.state.metrics_registry), media_type=CONTENT_TYPE_LATEST
    )
