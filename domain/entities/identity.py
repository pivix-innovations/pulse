from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.enums.identity import TenantRole
from domain.value_objects.ids import ApiKeyId, MembershipId, SessionId, TenantId, UserId


@dataclass(slots=True)
class Tenant:
    """A customer organization; every notification-data row hangs off one."""

    id: TenantId
    name: str
    slug: str
    is_active: bool = True
    # Ingress limits for POST /events. None means "the platform default".
    rate_limit_per_second: float | None = None
    rate_burst: int | None = None
    daily_quota: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class User:
    """
    A human who can sign in to the console. Platform admins operate across
    all tenants; everyone else acts through tenant memberships.
    """

    id: UserId
    email: str
    password_hash: str
    full_name: str = ""
    is_platform_admin: bool = False
    is_active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class Membership:
    id: MembershipId
    tenant_id: TenantId
    user_id: UserId
    role: TenantRole = TenantRole.MEMBER
    created_at: datetime | None = None


@dataclass(slots=True)
class ApiKey:
    """
    Machine ingress credential for one tenant. Only the hash is stored;
    the plaintext is shown once at mint time. `prefix` is the first
    characters of the plaintext, kept for display/identification.
    """

    id: ApiKeyId
    tenant_id: TenantId
    name: str
    prefix: str
    key_hash: str
    is_active: bool = True
    # What the key may do (see domain.value_objects.scopes). Keys created
    # before scopes existed hold all of them.
    scopes: tuple[str, ...] = ("ingest", "read", "client", "admin")
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    rotated_from_id: ApiKeyId | None = None   # the key this one replaced
    created_at: datetime | None = None

    def is_usable(self, now: datetime) -> bool:
        return self.is_active and (self.expires_at is None or self.expires_at > now)


@dataclass(slots=True)
class Session:
    """An opaque browser session; the cookie value's hash is stored."""

    id: SessionId
    user_id: UserId
    token_hash: str
    expires_at: datetime
    revoked_at: datetime | None = None
    created_at: datetime | None = None
