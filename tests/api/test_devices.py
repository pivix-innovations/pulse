from __future__ import annotations

REG = {"external_ref": "u1", "platform": "ios", "token": "apns-token-abcdef123456"}


async def test_a_registered_device_is_listed_with_a_masked_token_and_can_be_removed(world):
    created = await world.client.post("/api/v1/devices", json=REG, headers=world.key_headers)
    assert created.status_code == 201

    listed = await world.client.get("/api/v1/devices", params={"recipient": "u1"}, headers=world.key_headers)
    [row] = listed.json()
    assert row["id"] == created.json()["id"] and row["platform"] == "ios"
    assert row["token_hint"] == "…123456" and REG["token"] not in listed.text

    gone = await world.client.delete(f"/api/v1/devices/{row['id']}", headers=world.key_headers)
    assert gone.status_code == 204
    assert (await world.client.get("/api/v1/devices", params={"recipient": "u1"}, headers=world.key_headers)).json() == []
    assert (await world.client.delete(f"/api/v1/devices/{row['id']}", headers=world.key_headers)).status_code == 404


async def test_a_client_unregisters_by_token_and_repeating_it_is_harmless(world):
    await world.client.post("/api/v1/devices", json=REG, headers=world.key_headers)
    for _ in range(2):
        r = await world.client.request("DELETE", "/api/v1/devices", json={"token": REG["token"]}, headers=world.key_headers)
        assert r.status_code == 204
    assert (await world.client.get("/api/v1/devices", params={"recipient": "u1"}, headers=world.key_headers)).json() == []


async def test_listing_devices_for_an_unknown_recipient_is_empty(world):
    r = await world.client.get("/api/v1/devices", params={"recipient": "nobody"}, headers=world.key_headers)
    assert r.status_code == 200 and r.json() == []
