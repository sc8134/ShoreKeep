"""Phase 2: device token listing and revocation."""
from __future__ import annotations

from sqlmodel import Session, select

from backend.agent_auth import claim_pair, create_pair
from backend.db import engine
from backend.models import AgentDevice, User


def _register_and_login(client, email="dev@example.com", password="password123"):
    r = client.post("/v1/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return r.cookies.get("mp_session")


def _pair_device(client, cookie, label="Test Device"):
    """Create a pairing token and claim it, returning the device_token."""
    r = client.post(
        "/v1/agent/pair",
        headers={"origin": "http://127.0.0.1:8000"},
        cookies={"mp_session": cookie},
    )
    assert r.status_code == 201, r.text
    pair_token = r.json()["pair_token"]

    r2 = client.post("/v1/agent/pair/claim", json={"pair_token": pair_token, "label": label})
    assert r2.status_code == 200, r2.text
    return r2.json()["device_token"]


def test_list_devices_empty(client):
    cookie = _register_and_login(client)
    r = client.get("/v1/agent/devices", cookies={"mp_session": cookie})
    assert r.status_code == 200
    assert r.json() == []


def test_list_devices_after_pair(client):
    cookie = _register_and_login(client)
    _pair_device(client, cookie)
    r = client.get("/v1/agent/devices", cookies={"mp_session": cookie})
    assert r.status_code == 200
    devices = r.json()
    assert len(devices) == 1
    assert devices[0]["label"] == "Test Device"


def test_list_devices_requires_auth(client):
    r = client.get("/v1/agent/devices")
    assert r.status_code == 401


def test_revoke_device(client):
    cookie = _register_and_login(client)
    device_token = _pair_device(client, cookie)

    # find the device id
    r = client.get("/v1/agent/devices", cookies={"mp_session": cookie})
    device_id = r.json()[0]["id"]

    # revoke it
    r2 = client.delete(f"/v1/agent/devices/{device_id}", cookies={"mp_session": cookie})
    assert r2.status_code == 204

    # device list is now empty
    r3 = client.get("/v1/agent/devices", cookies={"mp_session": cookie})
    assert r3.json() == []


def test_revoked_device_token_gets_401(client):
    cookie = _register_and_login(client)
    device_token = _pair_device(client, cookie)

    r = client.get("/v1/agent/devices", cookies={"mp_session": cookie})
    device_id = r.json()[0]["id"]
    client.delete(f"/v1/agent/devices/{device_id}", cookies={"mp_session": cookie})

    # agent request with the now-revoked token should 401
    r2 = client.get("/v1/agent/me", headers={"Authorization": f"Bearer {device_token}"})
    assert r2.status_code == 401


def test_cannot_revoke_another_users_device(client):
    cookie_a = _register_and_login(client, "a@example.com")
    cookie_b = _register_and_login(client, "b@example.com")

    _pair_device(client, cookie_a, "A device")
    r = client.get("/v1/agent/devices", cookies={"mp_session": cookie_a})
    device_id = r.json()[0]["id"]

    # user B tries to revoke user A's device
    r2 = client.delete(f"/v1/agent/devices/{device_id}", cookies={"mp_session": cookie_b})
    assert r2.status_code == 404


def test_revoke_nonexistent_device_returns_404(client):
    cookie = _register_and_login(client)
    r = client.delete("/v1/agent/devices/doesnotexist", cookies={"mp_session": cookie})
    assert r.status_code == 404
