from __future__ import annotations

import pytest

from services.secrets import SecretBox, generate_key

SMTP = {
    "channel": "email",
    "name": "smtp",
    "label": "Marketing SMTP",
    "config": {"host": "smtp.acme.io", "from_address": "hi@acme.io"},
    "secrets": {"username": "apikey", "password": "s3cret-value"},
}


@pytest.fixture
def keyed(world):
    world.app.state.secret_box = SecretBox(generate_key())
    return world


async def create(world, body=SMTP):
    return await world.client.post("/api/v1/providers", json=body, headers=world.key_headers)


async def test_catalog_lists_providers_with_logo_and_fields(world):
    r = await world.client.get("/api/v1/provider-catalog", headers=world.key_headers)
    assert r.status_code == 200
    smtp = next(p for p in r.json() if p["id"] == "email/smtp")
    assert smtp["name"] == "SMTP" and smtp["logo_url"]
    assert {"key": "password", "secret": True}.items() <= next(
        f for f in smtp["fields"] if f["key"] == "password"
    ).items()

    logo = await world.client.get(smtp["logo_url"])  # <img> tags carry no credentials
    assert logo.status_code == 200 and logo.headers["content-type"].startswith("image/svg+xml")


async def test_catalog_can_filter_by_channel(world):
    r = await world.client.get(
        "/api/v1/provider-catalog?channel=push", headers=world.key_headers
    )
    assert {p["channel"] for p in r.json()} == {"push"}


async def test_secrets_are_write_only(keyed):
    r = await create(keyed)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["secrets_set"] == ["password", "username"]
    assert "s3cret-value" not in r.text
    assert body["catalog"]["name"] == "SMTP"

    listed = await keyed.client.get("/api/v1/providers", headers=keyed.key_headers)
    assert "s3cret-value" not in listed.text
    stored = next(iter(keyed.uow.providers.items.values()))
    assert "s3cret-value" not in stored.secrets_encrypted


async def test_storing_secrets_without_an_encryption_key_is_refused(world):
    r = await create(world)
    assert r.status_code == 503 and "SECRETS_KEY" in r.json()["detail"]
    assert not world.uow.providers.items


async def test_multiple_saved_providers_of_the_same_type(world):
    one = await create(world, {"channel": "sms", "name": "console", "label": "SMS A"})
    two = await create(world, {"channel": "sms", "name": "console", "label": "SMS B"})
    assert one.status_code == two.status_code == 201


async def test_validation_errors_are_reported_together(keyed):
    r = await create(keyed, {**SMTP, "config": {"port": "x"}, "secrets": {}})
    assert r.status_code == 422
    assert "host" in r.json()["detail"] and "port" in r.json()["detail"]


async def test_name_must_be_a_catalog_entry_for_that_channel(world):
    r = await create(world, {"channel": "sms", "name": "smtp"})
    assert r.status_code == 422 and "provider-catalog" in r.json()["detail"]


async def test_update_merges_secrets_and_keeps_omitted_ones(keyed):
    pid = (await create(keyed)).json()["id"]
    r = await keyed.client.put(
        f"/api/v1/providers/{pid}",
        json={**SMTP, "secrets": {"password": "rotated", "username": None}},
        headers=keyed.key_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["secrets_set"] == ["password"]
    assert keyed.app.state.secret_box.decrypt(
        keyed.uow.providers.items[pid].secrets_encrypted
    ) == {"password": "rotated"}


async def test_provider_type_cannot_change(keyed):
    pid = (await create(keyed)).json()["id"]
    r = await keyed.client.put(
        f"/api/v1/providers/{pid}",
        json={"channel": "sms", "name": "console"},
        headers=keyed.key_headers,
    )
    assert r.status_code == 422


# ── workflow chains ────────────────────────────────────────────────────


def workflow(chains, name="wf"):
    return {
        "name": name,
        "event_type": f"evt.{name}",
        "definition": {
            "channels": [{"channel": "email", "subject": "s", "body": "b", "format": "text"}],
            "provider_chains": chains,
        },
    }


async def make_wf(world, chains, name="wf"):
    return await world.client.post(
        "/api/v1/workflows", json=workflow(chains, name), headers=world.key_headers
    )


async def test_workflow_chain_keeps_the_given_priority_order(world):
    a = (await create(world, {"channel": "email", "name": "console", "label": "A"})).json()["id"]
    b = (await create(world, {"channel": "email", "name": "console", "label": "B"})).json()["id"]
    r = await make_wf(world, {"email": [b, a]})
    assert r.status_code == 201, r.text
    assert r.json()["definition"]["provider_chains"]["email"] == [b, a]


async def test_workflow_chain_rejects_unknown_wrong_channel_and_duplicate_providers(world):
    email = (await create(world, {"channel": "email", "name": "console", "label": "E"})).json()["id"]
    sms = (await create(world, {"channel": "sms", "name": "console", "label": "S"})).json()["id"]
    bogus = "00000000-0000-0000-0000-000000000009"
    assert (await make_wf(world, {"email": [bogus]})).status_code == 422
    assert (await make_wf(world, {"email": [sms]})).status_code == 422
    assert (await make_wf(world, {"email": [email, email]})).status_code == 422
    assert (await make_wf(world, {"fax": [email]})).status_code == 422


async def test_deleting_a_provider_in_use_is_blocked_and_usage_is_listed(world):
    pid = (await create(world, {"channel": "email", "name": "console", "label": "E"})).json()["id"]
    await make_wf(world, {"email": [pid]}, name="welcome")

    got = await world.client.get(f"/api/v1/providers/{pid}", headers=world.key_headers)
    assert [u["name"] for u in got.json()["used_by"]] == ["welcome"]

    r = await world.client.delete(f"/api/v1/providers/{pid}", headers=world.key_headers)
    assert r.status_code == 409 and "welcome" in r.json()["detail"]


async def test_tenant_cannot_chain_another_tenants_provider(world):
    from domain.entities.provider import Provider
    from domain.enums import ChannelType
    from domain.value_objects.ids import ProviderId, TenantId

    other = Provider(
        id=ProviderId.new(), tenant_id=TenantId.new(), channel=ChannelType.EMAIL,
        name="console", label="theirs",
    )
    await world.uow.providers.add(other)
    assert (await make_wf(world, {"email": [str(other.id)]})).status_code == 422


async def test_connection_test_uses_stored_secrets_when_form_leaves_them_blank(keyed, monkeypatch):
    from providers import catalog

    seen = {}

    async def fake_check(config, secrets):
        seen.update(config=config, secrets=secrets)
        return True, "looks good"

    spec = catalog.get_spec(catalog.ChannelType.EMAIL, "resend")
    monkeypatch.setitem(
        catalog._BY_ID, "email/resend", type(spec)(**{**{s: getattr(spec, s) for s in spec.__slots__}, "check": fake_check})
    )
    created = await create(keyed, {
        "channel": "email", "name": "resend", "config": {"from_address": "a@x.io"},
        "secrets": {"api_key": "re_stored"},
    })
    pid = created.json()["id"]
    r = await keyed.client.post(
        "/api/v1/provider-catalog/email/resend/test",
        json={"config": {"from_address": "b@x.io"}, "secrets": {}, "provider_id": pid},
        headers=keyed.key_headers,
    )
    assert r.json() == {"ok": True, "detail": "looks good"}
    assert seen["secrets"] == {"api_key": "re_stored"} and seen["config"]["from_address"] == "b@x.io"


async def test_connection_test_reports_validation_problems_instead_of_raising(world):
    r = await world.client.post(
        "/api/v1/provider-catalog/sms/twilio/test",
        json={"config": {}, "secrets": {}},
        headers=world.key_headers,
    )
    assert r.status_code == 200 and r.json()["ok"] is False
    assert "account_sid" in r.json()["detail"]


async def test_untestable_provider_returns_422(world):
    r = await world.client.post(
        "/api/v1/provider-catalog/sms/console/test", json={}, headers=world.key_headers
    )
    assert r.status_code == 422
