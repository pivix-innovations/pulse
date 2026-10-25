from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from domain.value_objects.ids import (
    DeliveryAttemptId,
    NotificationId,
    RecipientId,
    StepRunId,
    TenantId,
)


class StepRunStatus(str, Enum):
    SCHEDULED = "scheduled"   # waiting for run_at
    DONE = "done"             # ran: an attempt was created
    SKIPPED = "skipped"       # ran: condition false, recipient opted out, or the step is gone


@dataclass(slots=True)
class StepRun:
    """
    One recipient's progress through one step of a sequence workflow. Created
    when the previous step ran; the scheduler executes it once `run_at` passes.
    """

    id: StepRunId
    tenant_id: TenantId
    notification_id: NotificationId
    recipient_id: RecipientId
    step_id: str
    step_index: int
    run_at: datetime
    status: StepRunStatus = StepRunStatus.SCHEDULED
    attempt_id: DeliveryAttemptId | None = None
    reason: str | None = None
    executed_at: datetime | None = None
    created_at: datetime | None = None

    def mark_done(self, attempt_id: DeliveryAttemptId, at: datetime) -> None:
        self.status = StepRunStatus.DONE
        self.attempt_id = attempt_id
        self.executed_at = at

    def mark_skipped(self, reason: str, at: datetime) -> None:
        self.status = StepRunStatus.SKIPPED
        self.reason = reason
        self.executed_at = at
