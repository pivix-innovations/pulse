from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    ApiKeyModel,
    MembershipModel,
    SessionModel,
    TenantModel,
    UserModel,
)
from domain.entities.identity import ApiKey, Membership, Session, Tenant, User
from domain.enums.identity import TenantRole
from domain.value_objects.ids import ApiKeyId, MembershipId, SessionId, TenantId, UserId


def _tenant(row: TenantModel) -> Tenant:
    return Tenant(
        id=TenantId(row.id), name=row.name, slug=row.slug, is_active=row.is_active,
        rate_limit_per_second=row.rate_limit_per_second, rate_burst=row.rate_burst,
        daily_quota=row.daily_quota, created_at=row.created_at, updated_at=row.updated_at,
    )


def _user(row: UserModel) -> User:
    return User(
        id=UserId(row.id), email=row.email, password_hash=row.password_hash,
        full_name=row.full_name, is_platform_admin=row.is_platform_admin,
        is_active=row.is_active, created_at=row.created_at, updated_at=row.updated_at,
    )


def _membership(row: MembershipModel) -> Membership:
    return Membership(
        id=MembershipId(row.id), tenant_id=TenantId(row.tenant_id),
        user_id=UserId(row.user_id), role=TenantRole(row.role), created_at=row.created_at,
    )


def _api_key(row: ApiKeyModel) -> ApiKey:
    return ApiKey(
        id=ApiKeyId(row.id), tenant_id=TenantId(row.tenant_id), name=row.name,
        prefix=row.prefix, key_hash=row.key_hash, is_active=row.is_active,
        scopes=tuple(row.scopes or ()), expires_at=row.expires_at, last_used_at=row.last_used_at,
        rotated_from_id=ApiKeyId(row.rotated_from_id) if row.rotated_from_id else None,
        created_at=row.created_at,
    )


def _session(row: SessionModel) -> Session:
    return Session(
        id=SessionId(row.id), user_id=UserId(row.user_id), token_hash=row.token_hash,
        expires_at=row.expires_at, revoked_at=row.revoked_at, created_at=row.created_at,
    )


class PostgresTenantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, tenant: Tenant) -> None:
        self._session.add(TenantModel(
            id=tenant.id.value, name=tenant.name, slug=tenant.slug, is_active=tenant.is_active,
            rate_limit_per_second=tenant.rate_limit_per_second, rate_burst=tenant.rate_burst,
            daily_quota=tenant.daily_quota,
        ))

    async def get(self, tenant_id: TenantId) -> Tenant | None:
        row = await self._session.get(TenantModel, tenant_id.value)
        return _tenant(row) if row else None

    async def get_by_slug(self, slug: str) -> Tenant | None:
        row = (await self._session.execute(
            select(TenantModel).where(TenantModel.slug == slug)
        )).scalar_one_or_none()
        return _tenant(row) if row else None

    async def list(self) -> list[Tenant]:
        rows = (await self._session.execute(
            select(TenantModel).order_by(TenantModel.created_at)
        )).scalars().all()
        return [_tenant(r) for r in rows]

    async def update(self, tenant: Tenant) -> None:
        row = await self._session.get(TenantModel, tenant.id.value)
        if row is None:
            raise LookupError(f"Tenant {tenant.id} not found")
        row.name, row.slug, row.is_active = tenant.name, tenant.slug, tenant.is_active
        row.rate_limit_per_second = tenant.rate_limit_per_second
        row.rate_burst = tenant.rate_burst
        row.daily_quota = tenant.daily_quota


class PostgresUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, user: User) -> None:
        self._session.add(UserModel(
            id=user.id.value, email=user.email, password_hash=user.password_hash,
            full_name=user.full_name, is_platform_admin=user.is_platform_admin,
            is_active=user.is_active,
        ))

    async def get(self, user_id: UserId) -> User | None:
        row = await self._session.get(UserModel, user_id.value)
        return _user(row) if row else None

    async def get_by_email(self, email: str) -> User | None:
        row = (await self._session.execute(
            select(UserModel).where(UserModel.email == email.lower())
        )).scalar_one_or_none()
        return _user(row) if row else None

    async def list(self) -> list[User]:
        rows = (await self._session.execute(
            select(UserModel).order_by(UserModel.created_at)
        )).scalars().all()
        return [_user(r) for r in rows]

    async def count_platform_admins(self) -> int:
        rows = (await self._session.execute(
            select(UserModel.id).where(
                UserModel.is_platform_admin.is_(True), UserModel.is_active.is_(True)
            )
        )).all()
        return len(rows)

    async def update(self, user: User) -> None:
        row = await self._session.get(UserModel, user.id.value)
        if row is None:
            raise LookupError(f"User {user.id} not found")
        row.email = user.email
        row.password_hash = user.password_hash
        row.full_name = user.full_name
        row.is_platform_admin = user.is_platform_admin
        row.is_active = user.is_active


class PostgresMembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(self, membership: Membership) -> None:
        existing = (await self._session.execute(
            select(MembershipModel).where(
                MembershipModel.tenant_id == membership.tenant_id.value,
                MembershipModel.user_id == membership.user_id.value,
            )
        )).scalar_one_or_none()
        if existing is not None:
            existing.role = membership.role
            return
        self._session.add(MembershipModel(
            id=membership.id.value, tenant_id=membership.tenant_id.value,
            user_id=membership.user_id.value, role=membership.role,
        ))

    async def list_for_user(self, user_id: UserId) -> list[Membership]:
        rows = (await self._session.execute(
            select(MembershipModel).where(MembershipModel.user_id == user_id.value)
        )).scalars().all()
        return [_membership(r) for r in rows]

    async def list_for_tenant(self, tenant_id: TenantId) -> list[Membership]:
        rows = (await self._session.execute(
            select(MembershipModel).where(MembershipModel.tenant_id == tenant_id.value)
        )).scalars().all()
        return [_membership(r) for r in rows]

    async def get(self, tenant_id: TenantId, user_id: UserId) -> Membership | None:
        row = (await self._session.execute(
            select(MembershipModel).where(
                MembershipModel.tenant_id == tenant_id.value,
                MembershipModel.user_id == user_id.value,
            )
        )).scalar_one_or_none()
        return _membership(row) if row else None

    async def delete(self, membership_id: MembershipId) -> bool:
        result = await self._session.execute(
            delete(MembershipModel).where(MembershipModel.id == membership_id.value)
        )
        return result.rowcount > 0


class PostgresApiKeyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, key: ApiKey) -> None:
        self._session.add(ApiKeyModel(
            id=key.id.value, tenant_id=key.tenant_id.value, name=key.name,
            prefix=key.prefix, key_hash=key.key_hash, is_active=key.is_active,
            scopes=list(key.scopes), expires_at=key.expires_at,
            rotated_from_id=key.rotated_from_id.value if key.rotated_from_id else None,
        ))

    async def get_by_hash(self, key_hash: str) -> ApiKey | None:
        row = (await self._session.execute(
            select(ApiKeyModel).where(ApiKeyModel.key_hash == key_hash)
        )).scalar_one_or_none()
        return _api_key(row) if row else None

    async def list_for_tenant(self, tenant_id: TenantId) -> list[ApiKey]:
        rows = (await self._session.execute(
            select(ApiKeyModel)
            .where(ApiKeyModel.tenant_id == tenant_id.value)
            .order_by(ApiKeyModel.created_at)
        )).scalars().all()
        return [_api_key(r) for r in rows]

    async def get(self, tenant_id: TenantId, key_id: ApiKeyId) -> ApiKey | None:
        row = await self._session.get(ApiKeyModel, key_id.value)
        return _api_key(row) if row is not None and row.tenant_id == tenant_id.value else None

    async def set_expiry(self, tenant_id: TenantId, key_id: ApiKeyId, expires_at: datetime | None) -> bool:
        row = await self._session.get(ApiKeyModel, key_id.value)
        if row is None or row.tenant_id != tenant_id.value:
            return False
        row.expires_at = expires_at
        return True

    async def touch(self, key_id: ApiKeyId, at: datetime) -> None:
        row = await self._session.get(ApiKeyModel, key_id.value)
        if row is not None:
            row.last_used_at = at

    async def revoke(self, tenant_id: TenantId, key_id: ApiKeyId) -> bool:
        row = await self._session.get(ApiKeyModel, key_id.value)
        if row is None or row.tenant_id != tenant_id.value:
            return False
        row.is_active = False
        return True


class PostgresSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, sess: Session) -> None:
        self._session.add(SessionModel(
            id=sess.id.value, user_id=sess.user_id.value, token_hash=sess.token_hash,
            expires_at=sess.expires_at, revoked_at=sess.revoked_at,
        ))

    async def get_by_hash(self, token_hash: str) -> Session | None:
        row = (await self._session.execute(
            select(SessionModel).where(SessionModel.token_hash == token_hash)
        )).scalar_one_or_none()
        return _session(row) if row else None

    async def revoke(self, token_hash: str, when: datetime) -> None:
        row = (await self._session.execute(
            select(SessionModel).where(SessionModel.token_hash == token_hash)
        )).scalar_one_or_none()
        if row is not None and row.revoked_at is None:
            row.revoked_at = when

    async def delete_expired(self, now: datetime) -> int:
        result = await self._session.execute(
            delete(SessionModel).where(SessionModel.expires_at < now)
        )
        return result.rowcount
