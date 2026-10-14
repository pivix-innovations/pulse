from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TypeVar

_IdT = TypeVar("_IdT", bound="_Id")

class InvalidIdError(ValueError):
    """Raised when a string can't be parsed into a typed identifier"""
    
@dataclass(frozen=True, slots=True)
class _Id:
    value: uuid.UUID
    
    @classmethod
    def new(cls: type[_IdT]) -> _IdT:
        return cls(uuid.uuid4())
    
    @classmethod
    def from_str(cls: type[_IdT], raw: str) -> _IdT:
        try:
            return cls(uuid.UUID(raw))
        except (ValueError, AttributeError, TypeError) as exc:
            raise InvalidIdError(f"'{raw}' is not a valid {cls.__name__}") from exc
    
    def __str__(self):
        return str(self.value)


@dataclass(frozen=True, slots=True)
class TenantId(_Id):
    pass


@dataclass(frozen=True, slots=True)
class NotificationId(_Id):
    pass


@dataclass(frozen=True, slots=True)
class RecipientId(_Id):
    pass


@dataclass(frozen=True, slots=True)
class EventId(_Id):
    pass


@dataclass(frozen=True, slots=True)
class WorkflowId(_Id):
    pass


@dataclass(frozen=True, slots=True)
class TemplateId(_Id):
    pass


@dataclass(frozen=True, slots=True)
class ProviderId(_Id):
    pass


@dataclass(frozen=True, slots=True)
class PreferenceId(_Id):
    pass


@dataclass(frozen=True, slots=True)
class DeliveryAttemptId(_Id):
    pass


@dataclass(frozen=True, slots=True)
class DeviceTokenId(_Id):
    pass


@dataclass(frozen=True, slots=True)
class TemplateVersionId(_Id):
    pass


@dataclass(frozen=True, slots=True)
class DigestBatchId(_Id):
    pass


@dataclass(frozen=True, slots=True)
class DigestItemId(_Id):
    pass


@dataclass(frozen=True, slots=True)
class StepRunId(_Id):
    pass


@dataclass(frozen=True, slots=True)
class InboxMessageId(_Id):
    pass


@dataclass(frozen=True, slots=True)
class OutboxEntryId(_Id):
    pass


@dataclass(frozen=True, slots=True)
class DeadLetterId(_Id):
    pass

@dataclass(frozen=True, slots=True)
class UserId(_Id):
    pass


@dataclass(frozen=True, slots=True)
class MembershipId(_Id):
    pass


@dataclass(frozen=True, slots=True)
class ApiKeyId(_Id):
    pass


@dataclass(frozen=True, slots=True)
class SessionId(_Id):
    pass


@dataclass(frozen=True, slots=True)
class SuppressionId(_Id):
    pass


@dataclass(frozen=True, slots=True)
class AuditEntryId(_Id):
    pass
