from datetime import timedelta

from sqlmodel import Session, select

from backend.config import settings
from backend.db import engine
from backend.models import DesktopJob, utcnow

URL = "https://example.com/clip.mp4"


def _pair(client) -> str:
    token = client.post("/v1/agent/pair").json()["pair_token"]
    claim = client.post("/v1/agent/pair/claim", json={"pair_token": token})
    assert claim.status_code == 200, claim.text
    return claim.json()["device_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _reserve(client, token: str, job_id: str, preset: str = "720p", url: str = URL):
    return client.post(
        "/v1/agent/jobs",
        json={"job_id": job_id, "url": url, "preset": preset},
        headers=_auth(token),
    )


def _report(client, token: str, job_id: str, status: str = "done", size_bytes: int = 0, **extra):
    return client.put(
        f"/v1/agent/jobs/{job_id}",
        json={"status": status, "preset": "720p", "url": URL, "size_bytes": size_bytes, **extra},
        headers=_auth(token),
    )


def test_metering_requires_a_device_token(client):
    response = _reserve(client, "not-a-device-token", "job-00000001")
    assert response.status_code == 401


def test_reserve_and_complete_are_idempotent(client, register):
    register()
    token = _pair(client)

    start = _reserve(client, token, "job-00000001")
    assert start.status_code == 201, start.text
    assert start.json()["status"] == "running"
    assert start.json()["usage"]["used_this_hour"] == 1
    assert start.json()["usage"]["active_jobs"] == 1

    duplicate = _reserve(client, token, "job-00000001")
    assert duplicate.status_code == 200
    assert duplicate.json()["usage"]["used_this_hour"] == 1

    done = _report(client, token, "job-00000001", size_bytes=1234)
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "done"
    assert done.json()["usage"]["active_jobs"] == 0

    again = _report(client, token, "job-00000001", size_bytes=9999)
    assert again.status_code == 200
    assert again.json()["status"] == "done"

    with Session(engine) as session:
        row = session.exec(select(DesktopJob).where(DesktopJob.job_id == "job-00000001")).one()
        assert row.status == "done"
        assert row.size_bytes == 1234
        assert row.finished_at is not None

    me = client.get("/v1/auth/me").json()
    assert me["usage"]["used_this_hour"] == 1
    assert me["usage"]["active_jobs"] == 0


def test_desktop_jobs_count_toward_the_cloud_hourly_quota(client, register, monkeypatch):
    monkeypatch.setattr(settings, "free_jobs_per_user_hour", 2)
    register()
    token = _pair(client)

    assert _reserve(client, token, "job-00000001").status_code == 201
    assert _report(client, token, "job-00000001").status_code == 200

    second = _reserve(client, token, "job-00000002")
    assert second.status_code == 201
    assert second.json()["usage"]["used_this_hour"] == 2

    cloud = client.post("/v1/jobs", json={"url": URL, "preset": "720p"})
    assert cloud.status_code == 429
    assert "Hourly limit reached" in cloud.json()["detail"]


def test_cloud_jobs_count_toward_the_desktop_quota(client, register, monkeypatch):
    monkeypatch.setattr(settings, "free_jobs_per_user_hour", 2)
    register()
    token = _pair(client)

    assert _reserve(client, token, "job-00000001").status_code == 201
    assert _report(client, token, "job-00000001").status_code == 200
    assert client.post("/v1/jobs", json={"url": URL, "preset": "720p"}).status_code == 201

    denied = _reserve(client, token, "job-00000002")
    assert denied.status_code == 429
    assert "Hourly limit reached" in denied.json()["detail"]


def test_running_desktop_jobs_use_active_slots(client, register):
    register()
    token = _pair(client)

    assert _reserve(client, token, "job-00000001").status_code == 201
    assert _reserve(client, token, "job-00000002").status_code == 201
    busy = _reserve(client, token, "job-00000003")
    assert busy.status_code == 429
    assert "in progress" in busy.json()["detail"]

    assert _report(client, token, "job-00000001").status_code == 200
    assert _reserve(client, token, "job-00000003").status_code == 201


def test_stale_desktop_reservations_stop_counting(client, register, monkeypatch):
    monkeypatch.setattr(settings, "free_max_active_jobs", 1)
    register()
    token = _pair(client)

    assert _reserve(client, token, "job-00000001").status_code == 201
    assert _reserve(client, token, "job-00000002").status_code == 429

    with Session(engine) as session:
        row = session.exec(select(DesktopJob).where(DesktopJob.job_id == "job-00000001")).one()
        row.created_at = utcnow() - timedelta(seconds=settings.job_timeout_seconds + 60)
        session.add(row)
        session.commit()

    assert _reserve(client, token, "job-00000002").status_code == 201


def test_report_upserts_usage_from_an_offline_client(client, register):
    register()
    token = _pair(client)

    response = _report(
        client,
        token,
        "job-offline-1",
        size_bytes=42,
        started_at=(utcnow() - timedelta(minutes=5)).isoformat(),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "done"
    assert response.json()["usage"]["used_this_hour"] == 1

    repeated = _report(client, token, "job-offline-1", size_bytes=42)
    assert repeated.status_code == 200
    assert repeated.json()["usage"]["used_this_hour"] == 1


def test_reserve_rejects_private_targets_and_unknown_presets(client, register):
    register()
    token = _pair(client)

    private = _reserve(client, token, "job-00000001", url="http://127.0.0.1:8000/internal")
    assert private.status_code == 400
    assert "non-public" in private.json()["detail"]

    preset = _reserve(client, token, "job-00000002", preset="4k")
    assert preset.status_code == 400


def test_device_me_includes_usage(client, register):
    register()
    token = _pair(client)
    assert _reserve(client, token, "job-00000001").status_code == 201

    me = client.get("/v1/agent/me", headers=_auth(token))
    assert me.status_code == 200
    assert me.json()["usage"]["used_this_hour"] == 1
    assert me.json()["usage"]["active_jobs"] == 1
