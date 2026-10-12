from __future__ import annotations

from domain.entities.identity import Membership, User
from domain.enums.identity import TenantRole
from domain.value_objects.ids import MembershipId, TenantId, UserId
from services.identity_service import (
    AuthenticatedUser,
    can_access_tenant,
    hash_password,
    is_tenant_admin,
    mint_api_key_token,
    token_hash,
    verify_password,
)


def _user(*, platform_admin: bool = False) -> User:
    return User(
        id=UserId.new(), email="u@x.test", password_hash="", is_platform_admin=platform_admin
    )


def _auth(user: User, memberships: list[Membership]) -> AuthenticatedUser:
    return AuthenticatedUser(user=user, memberships=memberships)


class TestPasswords:
    def test_roundtrip(self):
        h = hash_password("correct horse")
        assert verify_password("correct horse", h)
        assert not verify_password("wrong", h)

    def test_hashes_are_salted(self):
        assert hash_password("same") != hash_password("same")

    def test_garbage_stored_hash_never_verifies(self):
        assert not verify_password("x", "not-a-hash")
        assert not verify_password("x", "")


class TestApiKeyTokens:
    def test_mint_shape_and_hash(self):
        t = mint_api_key_token()
        assert t.startswith("plk_")
        assert len(token_hash(t)) == 64
        assert token_hash(t) == token_hash(t)
        assert token_hash(t) != token_hash(mint_api_key_token())


class TestTenantAccess:
    def test_platform_admin_accesses_any_tenant(self):
        auth = _auth(_user(platform_admin=True), [])
        anywhere = TenantId.new()
        assert can_access_tenant(auth, anywhere)
        assert is_tenant_admin(auth, anywhere)

    def test_member_accesses_only_their_tenant(self):
        user = _user()
        home = TenantId.new()
        auth = _auth(user, [Membership(
            id=MembershipId.new(), tenant_id=home, user_id=user.id, role=TenantRole.MEMBER
        )])
        assert can_access_tenant(auth, home)
        assert not is_tenant_admin(auth, home)
        assert not can_access_tenant(auth, TenantId.new())

    def test_tenant_admin_is_admin_only_at_home(self):
        user = _user()
        home = TenantId.new()
        auth = _auth(user, [Membership(
            id=MembershipId.new(), tenant_id=home, user_id=user.id, role=TenantRole.ADMIN
        )])
        assert is_tenant_admin(auth, home)
        assert not is_tenant_admin(auth, TenantId.new())
