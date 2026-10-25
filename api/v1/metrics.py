from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from api.dependencies import require_scope
from domain.value_objects.ids import TenantId

router = APIRouter(prefix="/metrics", tags=["metrics"])


class TimelinePoint(BaseModel):
    bucket: datetime               # start of the bucket
    counts: dict[str, int]         # attempts created in it, by status


class MetricsOut(BaseModel):
    window_hours: int
    notifications: dict[str, int]
    attempts: dict[str, dict[str, int]]
    dead_letters: int
    avg_confirmation_seconds: float | None
    bucket_seconds: int
    timeline: list[TimelinePoint]


@router.get("", response_model=MetricsOut)
async def get_metrics(
    request: Request,
    window_hours: int = Query(default=24, ge=1, le=24 * 30),
    tenant_id: TenantId = Depends(require_scope("read")),
) -> MetricsOut:
    summary = await request.app.state.metrics_service.summary(tenant_id, window_hours)
    return MetricsOut(
        window_hours=summary.window_hours,
        notifications=summary.notifications,
        attempts=summary.attempts,
        dead_letters=summary.dead_letters,
        avg_confirmation_seconds=summary.avg_confirmation_seconds,
        bucket_seconds=summary.bucket_seconds,
        timeline=[TimelinePoint(bucket=b, counts=c) for b, c in summary.timeline],
    )
