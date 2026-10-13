from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from domain.value_objects.ids import TenantId


@dataclass(frozen=True, slots=True)
class ResolvedRecipient:
    """Recipient data as returned by Sentinel (not yet a local Recipient row)."""

    external_ref: str
    email: str | None = None
    phone: str | None = None
    locale: str = "en"
    timezone: str = "UTC"


class SentinelUnavailable(Exception):
    """
    Sentinel could not answer (down, timing out, breaker open, bad credentials
    or an unusable response). This is *not* "no recipients": callers must
    retry later rather than treat the event as having nobody to notify.
    """

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class SentinelClient(Protocol):
    """Port for the Sentinel user/tenant service (contract: docs/sentinel-contract.md)."""

    async def resolve_recipients(
        self, tenant_id: TenantId, event_type: str, payload: Mapping[str, Any]
    ) -> list[ResolvedRecipient]: ...
