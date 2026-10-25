from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from domain.enums import ChannelType
from domain.value_objects.ids import DeliveryAttemptId, ProviderId, TenantId

# "recipient_error": the provider worked, the destination didn't (bad number,
# refused address). It is logged but never counted against the provider.
CallOutcome = Literal["ok", "provider_error", "recipient_error"]


@dataclass(slots=True)
class ProviderCall:
    """One call to a saved provider during a delivery; the raw material for provider health."""

    tenant_id: TenantId
    provider_id: ProviderId
    attempt_id: DeliveryAttemptId
    channel: ChannelType
    outcome: CallOutcome
    latency_ms: int
    error: str | None = None
    created_at: datetime | None = None
