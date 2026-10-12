from __future__ import annotations

from tests.api.conftest import PASSWORD


async def test_login_sets_httponly_session_cookie_and_returns_memberships(world):
    res = await world.client.post(
        "/api/v1/auth/login", json={"email": "ada@example.com", "password": PASSWORD}
    )

    assert res.status_code == 200
    body = res.json()
    assert body["email"] == "ada@example.com"
    assert [m["tenant_slug"] for m in body["memberships"]] == ["acme"]
    cookie = res.headers["set-cookie"].lower()
    assert "pulse_session=" in cookie and "httponly" in cookie


async def test_wrong_password_is_401(world):
    res = await world.client.post(
        "/api/v1/auth/login", json={"email": "ada@example.com", "password": "nope"}
    )
    assert res.status_code == 401


async def test_me_requires_a_session_then_works_with_cookie(world):
    assert (await world.client.get("/api/v1/auth/me")).status_code == 401

    await world.client.post(
        "/api/v1/auth/login", json={"email": "ada@example.com", "password": PASSWORD}
    )
    res = await world.client.get("/api/v1/auth/me")

    assert res.status_code == 200
    assert res.json()["email"] == "ada@example.com"


async def test_logout_revokes_the_session(world):
    await world.client.post(
        "/api/v1/auth/login", json={"email": "ada@example.com", "password": PASSWORD}
    )
    assert (await world.client.post("/api/v1/auth/logout")).status_code == 204
    assert (await world.client.get("/api/v1/auth/me")).status_code == 401


async def test_api_key_and_tenant_header_auth(world):
    assert (await world.client.get("/api/v1/notifications")).status_code == 401
    bad = await world.client.get("/api/v1/notifications", headers={"X-API-Key": "pk_wrong"})
    assert bad.status_code == 401
    ok = await world.client.get("/api/v1/notifications", headers=world.key_headers)
    assert ok.status_code == 200

    # Console path: session cookie + X-Tenant-Id, membership enforced.
    await world.client.post(
        "/api/v1/auth/login", json={"email": "ada@example.com", "password": PASSWORD}
    )
    member = await world.client.get(
        "/api/v1/notifications", headers={"X-Tenant-Id": str(world.tenant_id)}
    )
    assert member.status_code == 200
    stranger = await world.client.get(
        "/api/v1/notifications",
        headers={"X-Tenant-Id": "00000000-0000-0000-0000-000000000001"},
    )
    assert stranger.status_code == 403
