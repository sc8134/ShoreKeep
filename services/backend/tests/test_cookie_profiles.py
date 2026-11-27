"""Phase 4: cookie profiles — create, list, delete, use in job."""
from __future__ import annotations

VALID_COOKIES = "\n".join([
    "# Netscape HTTP Cookie File",
    "youtube.com\tFALSE\t/\tTRUE\t9999999999\tSID\tabc123",
])


def _register(client, email="cp@example.com", password="password123"):
    r = client.post("/v1/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return r.cookies.get("mp_session")


def test_list_profiles_empty(client):
    cookie = _register(client)
    r = client.get("/v1/cookies/profiles", cookies={"mp_session": cookie})
    assert r.status_code == 200
    assert r.json() == []


def test_create_profile(client):
    cookie = _register(client)
    r = client.post(
        "/v1/cookies/profiles",
        json={"name": "My YT", "domain": "youtube.com", "cookies": VALID_COOKIES},
        cookies={"mp_session": cookie},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "My YT"
    assert data["domain"] == "youtube.com"
    assert "id" in data
    # cookies_enc must NOT be in response
    assert "cookies_enc" not in data
    assert "cookies" not in data


def test_list_profiles_after_create(client):
    cookie = _register(client)
    client.post(
        "/v1/cookies/profiles",
        json={"name": "P1", "domain": "youtube.com", "cookies": VALID_COOKIES},
        cookies={"mp_session": cookie},
    )
    r = client.get("/v1/cookies/profiles", cookies={"mp_session": cookie})
    assert len(r.json()) == 1


def test_delete_profile(client):
    cookie = _register(client)
    r = client.post(
        "/v1/cookies/profiles",
        json={"name": "P1", "domain": "youtube.com", "cookies": VALID_COOKIES},
        cookies={"mp_session": cookie},
    )
    pid = r.json()["id"]
    r2 = client.delete(f"/v1/cookies/profiles/{pid}", cookies={"mp_session": cookie})
    assert r2.status_code == 204
    r3 = client.get("/v1/cookies/profiles", cookies={"mp_session": cookie})
    assert r3.json() == []


def test_cannot_access_other_users_profile(client):
    cookie_a = _register(client, "cpa@example.com")
    cookie_b = _register(client, "cpb@example.com")

    r = client.post(
        "/v1/cookies/profiles",
        json={"name": "A profile", "domain": "youtube.com", "cookies": VALID_COOKIES},
        cookies={"mp_session": cookie_a},
    )
    pid = r.json()["id"]

    r2 = client.delete(f"/v1/cookies/profiles/{pid}", cookies={"mp_session": cookie_b})
    assert r2.status_code == 404


def test_max_10_profiles(client):
    cookie = _register(client)
    for i in range(10):
        r = client.post(
            "/v1/cookies/profiles",
            json={"name": f"P{i}", "domain": "youtube.com", "cookies": VALID_COOKIES},
            cookies={"mp_session": cookie},
        )
        assert r.status_code == 201

    r11 = client.post(
        "/v1/cookies/profiles",
        json={"name": "P10", "domain": "youtube.com", "cookies": VALID_COOKIES},
        cookies={"mp_session": cookie},
    )
    assert r11.status_code == 400


def test_requires_auth(client):
    r = client.get("/v1/cookies/profiles")
    assert r.status_code == 401


def test_create_requires_auth(client):
    r = client.post(
        "/v1/cookies/profiles",
        json={"name": "P", "domain": "youtube.com", "cookies": VALID_COOKIES},
    )
    assert r.status_code == 401


def test_invalid_cookies_rejected(client):
    cookie = _register(client)
    r = client.post(
        "/v1/cookies/profiles",
        json={"name": "Bad", "domain": "youtube.com", "cookies": "not a cookie file at all!!"},
        cookies={"mp_session": cookie},
    )
    assert r.status_code == 400
