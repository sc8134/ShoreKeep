from sqlmodel import Session, select

from backend.db import engine
from backend.models import Job, User

VIDEO_URL = "https://example.com/clip.mp4"


def _create_job(client, preset: str = "720p", **extra):
    return client.post("/v1/jobs", json={"url": VIDEO_URL, "preset": preset, **extra})


def test_register_sets_session_and_returns_usage(client):
    response = client.post(
        "/v1/auth/register", json={"email": "  New@Example.com ", "password": "password123"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["user"]["email"] == "new@example.com"
    assert body["user"]["plan"] == "free"
    assert body["usage"]["limit_per_hour"] > 0
    assert "best" in body["usage"]["presets"]
    assert "mp_session" in response.cookies

    me = client.get("/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "new@example.com"


def test_duplicate_email_and_weak_password(client, register):
    register("dup@example.com")
    again = client.post(
        "/v1/auth/register", json={"email": "dup@example.com", "password": "password123"}
    )
    assert again.status_code == 409

    weak = client.post("/v1/auth/register", json={"email": "x@example.com", "password": "short"})
    assert weak.status_code == 400


def test_login_logout_flow(client, register):
    register("login@example.com", "password123")
    client.post("/v1/auth/logout")
    assert client.get("/v1/auth/me").status_code == 401

    bad = client.post(
        "/v1/auth/login", json={"email": "login@example.com", "password": "wrong-password"}
    )
    assert bad.status_code == 401

    good = client.post(
        "/v1/auth/login", json={"email": "Login@Example.com", "password": "password123"}
    )
    assert good.status_code == 200
    assert client.get("/v1/auth/me").status_code == 200


def test_me_requires_auth(client):
    assert client.get("/v1/auth/me").status_code == 401


def test_bearer_token_auth(client, register):
    from fastapi.testclient import TestClient

    from backend.api.main import app
    from backend.auth import create_session

    register("bearer@example.com")
    with Session(engine) as session:
        user = session.exec(select(User).where(User.email == "bearer@example.com")).first()
        assert user is not None
        token = create_session(session, user)

    with TestClient(app) as fresh:
        response = fresh.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json()["user"]["email"] == "bearer@example.com"


def test_anonymous_preset_cap(client):
    response = _create_job(client, "1080p")
    assert response.status_code == 403
    assert "720p" in response.json()["detail"]

    allowed = _create_job(client, "720p")
    assert allowed.status_code == 201
    assert allowed.json()["status"] == "queued"


def test_cookies_require_login(client):
    cookies = ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\tabc\n"
    response = _create_job(client, "720p", cookies=cookies)
    assert response.status_code == 403
    assert "Sign in" in response.json()["detail"]


def test_invalid_cookies_rejected_when_signed_in(client, register):
    register("cookies@example.com")
    response = _create_job(client, "720p", cookies="not a cookie file")
    assert response.status_code == 400

    valid = _create_job(
        client, "720p", cookies=".youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\tabc\n"
    )
    assert valid.status_code == 201

    with Session(engine) as session:
        job = session.get(Job, valid.json()["id"])
        assert job is not None
        assert job.cookies_enc and job.cookies_enc.startswith("gAAAA")
        assert "SID" not in job.cookies_enc


def test_anonymous_job_is_public_by_id(client):
    created = _create_job(client, "720p")
    assert created.status_code == 201
    job_id = created.json()["id"]

    from fastapi.testclient import TestClient

    from backend.api.main import app

    with TestClient(app) as stranger:
        assert stranger.get(f"/v1/jobs/{job_id}").status_code == 200


def test_job_ownership_is_enforced(client, register):
    from fastapi.testclient import TestClient

    from backend.api.main import app

    register("a@example.com")
    created = _create_job(client, "1080p")
    assert created.status_code == 201
    job_id = created.json()["id"]

    with TestClient(app) as other:
        other.post(
            "/v1/auth/register", json={"email": "b@example.com", "password": "password123"}
        )
        assert other.get(f"/v1/jobs/{job_id}").status_code == 404
        assert other.get(f"/v1/jobs/{job_id}/file").status_code == 404
        assert other.get("/v1/jobs").json() == []

    assert client.get(f"/v1/jobs/{job_id}").status_code == 200
    listed = client.get("/v1/jobs").json()
    assert [job["id"] for job in listed] == [job_id]


def test_job_list_requires_auth(client):
    assert client.get("/v1/jobs").status_code == 401


def test_anonymous_hourly_quota(client, monkeypatch):
    from backend.config import settings

    monkeypatch.setattr(settings, "anon_jobs_per_ip_hour", 1)
    monkeypatch.setattr(settings, "anon_max_active_jobs", 10)

    assert _create_job(client, "720p").status_code == 201
    blocked = _create_job(client, "720p")
    assert blocked.status_code == 429
    assert "Hourly limit" in blocked.json()["detail"]


def test_anonymous_concurrency_limit(client, monkeypatch):
    from backend.config import settings

    monkeypatch.setattr(settings, "anon_max_active_jobs", 1)

    assert _create_job(client, "720p").status_code == 201
    blocked = _create_job(client, "720p")
    assert blocked.status_code == 429
    assert "in progress" in blocked.json()["detail"]


def test_free_user_hourly_quota(client, register, monkeypatch):
    from backend.config import settings

    register("quota@example.com")
    monkeypatch.setattr(settings, "free_jobs_per_user_hour", 1)
    monkeypatch.setattr(settings, "free_max_active_jobs", 10)

    assert _create_job(client, "1080p").status_code == 201
    blocked = _create_job(client, "1080p")
    assert blocked.status_code == 429
    assert "Hourly limit" in blocked.json()["detail"]


def test_cookie_cross_origin_post_blocked(client, register):
    register("csrf@example.com")
    response = client.post(
        "/v1/jobs",
        json={"url": VIDEO_URL, "preset": "720p"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403

    same_site = client.post(
        "/v1/jobs",
        json={"url": VIDEO_URL, "preset": "720p"},
        headers={"Origin": "http://testserver"},
    )
    assert same_site.status_code == 201


def test_turnstile_required_when_enabled(client, monkeypatch):
    from backend.config import settings

    monkeypatch.setattr(settings, "turnstile_enabled", True)
    monkeypatch.setattr(settings, "turnstile_secret", "test-secret")

    blocked = _create_job(client, "720p")
    assert blocked.status_code == 400
    assert "bot check" in blocked.json()["detail"].lower()


def test_public_config(client):
    body = client.get("/v1/config").json()
    assert body["turnstile"]["enabled"] is False
    assert body["limits"]["anon_max_preset"] == "720p"
    assert body["limits"]["retention_seconds"] > 0
