from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from domain.entities.identity import ApiKey, Membership, Session, User
from domain.enums.identity import TenantRole
from domain.value_objects.scopes import ALL_SCOPES
from domain.value_objects.ids import ApiKeyId, SessionId, TenantId
from services.ports.uow import UnitOfWorkFactory

SESSION_TTL = timedelta(days=7)
API_KEY_BYTES = 24
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


LAST_USED_RESOLUTION = timedelta(minutes=1)   # "last used" is written at most this often per key


@dataclass(frozen=True, slots=True)
class ApiPrincipal:
    """Who an API key speaks for, and what it may do."""

    tenant_id: TenantId
    scopes: frozenset[str]
    key_id: ApiKeyId | None = None   # None for env-configured bootstrap keys


class ApiKeyError(Exception):
    """A key operation that cannot be done (unknown scope, already revoked, ...)."""


class AuthenticationError(Exception):
    """Bad credentials, or an expired/revoked session."""


# ── Password hashing (stdlib scrypt; format: scrypt$n$r$p$salt$hash) ──

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, hash_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p)
        )
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


# ── Opaque tokens (sessions + API keys): sha256 stored, plaintext shown once ──

def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def mint_api_key_token() -> str:
    return "plk_" + secrets.token_urlsafe(API_KEY_BYTES)


@dataclass(slots=True)
class AuthenticatedUser:
    user: User
    memberships: list[Membership]


class IdentityService:
    """Login, sessions, and API-key issuance over the identity tables."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory
        self._last_touched: dict[ApiKeyId, datetime] = {}

    async def login(self, email: str, password: str) -> tuple[str, AuthenticatedUser]:
        """Returns (session token to set as cookie, authenticated user)."""
        async with self._uow_factory() as uow:
            user = await uow.users.get_by_email(email.strip().lower())
            # Verify against a dummy hash on unknown users so response
            # timing does not reveal which emails exist.
            stored = user.password_hash if user else hash_password(secrets.token_hex(8))
            if not verify_password(password, stored) or user is None or not user.is_active:
                raise AuthenticationError("Invalid email or password")

            token = secrets.token_urlsafe(32)
            await uow.sessions.add(Session(
                id=SessionId.new(),
                user_id=user.id,
                token_hash=token_hash(token),
                expires_at=datetime.now(timezone.utc) + SESSION_TTL,
            ))
            memberships = await uow.memberships.list_for_user(user.id)
            await uow.commit()
            return token, AuthenticatedUser(user=user, memberships=memberships)

    async def logout(self, token: str) -> None:
        async with self._uow_factory() as uow:
            await uow.sessions.revoke(token_hash(token), datetime.now(timezone.utc))
            await uow.commit()

    async def resolve_session(self, token: str) -> AuthenticatedUser:
        async with self._uow_factory() as uow:
            sess = await uow.sessions.get_by_hash(token_hash(token))
            now = datetime.now(timezone.utc)
            if sess is None or sess.revoked_at is not None or sess.expires_at < now:
                raise AuthenticationError("Session expired or revoked")
            user = await uow.users.get(sess.user_id)
            if user is None or not user.is_active:
                raise AuthenticationError("User is disabled")
            memberships = await uow.memberships.list_for_user(user.id)
            return AuthenticatedUser(user=user, memberships=memberships)

    async def resolve_api_key(self, raw_key: str) -> ApiPrincipal | None:
        """DB-backed key lookup: the tenant and scopes it carries, or None if unusable."""
        now = datetime.now(timezone.utc)
        async with self._uow_factory() as uow:
            key = await uow.api_keys.get_by_hash(token_hash(raw_key))
            if key is None or not key.is_usable(now):          # revoked or expired
                return None
            tenant = await uow.tenants.get(key.tenant_id)
            if tenant is None or not tenant.is_active:
                return None
            await self._touch(uow, key, now)
            return ApiPrincipal(key.tenant_id, frozenset(key.scopes), key.id)

    async def _touch(self, uow, key: ApiKey, now: datetime) -> None:
        """Record "last used", at most once a minute per key, never failing the request."""
        last = self._last_touched.get(key.id)
        if last is not None and now - last < LAST_USED_RESOLUTION:
            return
        self._last_touched[key.id] = now
        try:
            await uow.api_keys.touch(key.id, now)
            await uow.commit()
        except Exception:
            self._last_touched.pop(key.id, None)

    async def create_api_key(
        self,
        tenant_id: TenantId,
        name: str,
        *,
        scopes: tuple[str, ...] | list[str] | None = None,
        expires_at: datetime | None = None,
        rotated_from: ApiKeyId | None = None,
    ) -> tuple[str, ApiKey]:
        """Returns (plaintext key, shown exactly once; stored key row). Default scopes: all."""
        chosen = tuple(dict.fromkeys(scopes)) if scopes is not None else tuple(sorted(ALL_SCOPES))
        unknown = set(chosen) - ALL_SCOPES
        if unknown or not chosen:
            raise ApiKeyError(
                f"Unknown scope(s): {', '.join(sorted(unknown))}" if unknown else "A key needs at least one scope"
            )
        token = mint_api_key_token()
        key = ApiKey(
            id=ApiKeyId.new(),
            tenant_id=tenant_id,
            name=name,
            prefix=token[:12],
            key_hash=token_hash(token),
            scopes=chosen,
            expires_at=expires_at,
            rotated_from_id=rotated_from,
        )
        async with self._uow_factory() as uow:
            await uow.api_keys.add(key)
            await uow.commit()
        return token, key

    async def rotate_api_key(
        self, tenant_id: TenantId, key_id: ApiKeyId, *, grace: timedelta = timedelta(hours=24)
    ) -> tuple[str, ApiKey, datetime | None]:
        """
        Replace a key with a fresh one holding the same scopes (and the same
        expiry, if it had one). The old key keeps working for `grace` so
        clients can switch over without downtime; zero revokes it at once.
        Returns (new plaintext, new key row, when the old key stops working).
        """
        now = datetime.now(timezone.utc)
        async with self._uow_factory() as uow:
            old = await uow.api_keys.get(tenant_id, key_id)
            if old is None:
                raise LookupError("API key not found")
            if not old.is_usable(now):
                raise ApiKeyError("Only an active key can be rotated")
            # The replacement inherits the original lifetime, not the wind-down date below.
            name, scopes, original_expiry = old.name, old.scopes, old.expires_at
            retire_at = None if grace <= timedelta(0) else now + grace
            if original_expiry is not None and retire_at is not None:
                retire_at = min(retire_at, original_expiry)
            if retire_at is None:
                await uow.api_keys.revoke(tenant_id, key_id)
            else:
                await uow.api_keys.set_expiry(tenant_id, key_id, retire_at)
            await uow.commit()
        token, new = await self.create_api_key(
            tenant_id, name, scopes=scopes, expires_at=original_expiry, rotated_from=key_id
        )
        return token, new, retire_at if retire_at is not None else now


def can_access_tenant(auth: AuthenticatedUser, tenant_id: TenantId) -> bool:
    if auth.user.is_platform_admin:
        return True
    return any(m.tenant_id == tenant_id for m in auth.memberships)


def is_tenant_admin(auth: AuthenticatedUser, tenant_id: TenantId) -> bool:
    if auth.user.is_platform_admin:
        return True
    return any(
        m.tenant_id == tenant_id and m.role is TenantRole.ADMIN for m in auth.memberships
    )
