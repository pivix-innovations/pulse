from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from api.dependencies import require_tenant
from domain.value_objects.ids import TenantId

router = APIRouter(prefix="/metrics", tags=["metrics"])


class MetricsOut(BaseModel):
    window_hours: int
    notifications: dict[str, int]
    attempts: dict[str, dict[str, int]]
    dead_letters: int
    avg_confirmation_seconds: float | None


@router.get("", response_model=MetricsOut)
async def get_metrics(
    request: Request,
    window_hours: int = Query(default=24, ge=1, le=24 * 30),
    tenant_id: TenantId = Depends(require_tenant),
) -> MetricsOut:
    summary = await request.app.state.metrics_service.summary(tenant_id, window_hours)
    return MetricsOut(
        window_hours=summary.window_hours,
        notifications=summary.notifications,
        attempts=summary.attempts,
        dead_letters=summary.dead_letters,
        avg_confirmation_seconds=summary.avg_confirmation_seconds,
    )
