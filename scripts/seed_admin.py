"""
Bootstrap the first platform admin (and optionally a first tenant).

    uv run python -m scripts.seed_admin [email] [password] [tenant-name]

Falls back to PULSE_BOOTSTRAP_EMAIL / PULSE_BOOTSTRAP_PASSWORD /
PULSE_BOOTSTRAP_TENANT, then to dev defaults. Idempotent: existing rows
are left untouched.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys

from config.loader import get_settings
from database.database import Database
from domain.entities.identity import Membership, Tenant, User
from domain.enums.identity import TenantRole
from domain.value_objects.ids import MembershipId, TenantId, UserId
from repositories.uow import SqlAlchemyUnitOfWork
from services.identity_service import hash_password


async def main() -> None:
    args = sys.argv[1:]
    email = (args[0] if len(args) > 0 else os.environ.get("PULSE_BOOTSTRAP_EMAIL", "admin@pulse.local")).lower()
    password = args[1] if len(args) > 1 else os.environ.get("PULSE_BOOTSTRAP_PASSWORD", "admin12345")
    tenant_name = args[2] if len(args) > 2 else os.environ.get("PULSE_BOOTSTRAP_TENANT", "Acme")

    database = Database(get_settings().database)
    await database.initialize()
    try:
        async with SqlAlchemyUnitOfWork(database.session_factory) as uow:
            user = await uow.users.get_by_email(email)
            if user is None:
                user = User(
                    id=UserId.new(), email=email,
                    password_hash=hash_password(password),
                    full_name="Platform Admin", is_platform_admin=True,
                )
                await uow.users.add(user)
                print(f"created platform admin {email}")
            else:
                print(f"platform admin {email} already exists — leaving as is")

            slug = re.sub(r"[^a-z0-9-]+", "-", tenant_name.lower()).strip("-") or "default"
            tenant = await uow.tenants.get_by_slug(slug)
            if tenant is None:
                tenant = Tenant(id=TenantId.new(), name=tenant_name, slug=slug)
                await uow.tenants.add(tenant)
                print(f"created tenant '{tenant_name}' ({tenant.id})")
            else:
                print(f"tenant '{slug}' already exists — leaving as is")

            await uow.memberships.upsert(Membership(
                id=MembershipId.new(), tenant_id=tenant.id, user_id=user.id,
                role=TenantRole.ADMIN,
            ))
            await uow.commit()
        print(f"\nSign in at /ui/ with {email}")
    finally:
        await database.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
