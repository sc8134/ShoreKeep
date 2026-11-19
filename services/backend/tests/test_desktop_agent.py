import pytest

pytest.importorskip("mydl_desktop")

from fastapi.testclient import TestClient

from mydl_desktop.config import AgentConfig
from mydl_desktop.jobs import JobManager
from mydl_desktop.server import PairRegistry, create_app


@pytest.fixture
def agent_client(tmp_path, monkeypatch):
    def fake_claim(self, token):
        return {
            "user": {"id": "u1", "email": "desk@example.com", "plan": "free"},
            "limits": {"plan": "free", "max_preset": None, "presets": ["best", "audio"]},
            "device_token": "device-token",
        }

    monkeypatch.setattr(PairRegistry, "_claim", fake_claim)
    config = AgentConfig(
        download_dir=str(tmp_path / "downloads"),
        state_dir=str(tmp_path / "state"),
        device_token="device-token",
    )
    app = create_app(config, JobManager(config))
    with TestClient(app, base_url="http://127.0.0.1:47821") as client:
        yield client


def test_health_reports_browsers(agent_client):
    response = agent_client.get("/health")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert any(item["key"] == "firefox" for item in body["browsers"])


def test_host_guard_blocks_rebinding(agent_client):
    response = agent_client.get("/health", headers={"host": "attacker.example"})
    assert response.status_code == 403


def test_run_requires_pairing(agent_client):
    response = agent_client.post(
        "/v1/run", json={"url": "https://example.com/video", "preset": "best"}
    )
    assert response.status_code == 401


def test_run_rejects_private_targets(agent_client):
    response = agent_client.post(
        "/v1/run",
        json={"url": "http://127.0.0.1:8000/internal", "preset": "best"},
        headers={"X-Agent-Key": "k" * 40},
    )
    assert response.status_code == 400
    assert "non-public" in response.json()["detail"]


def test_run_enforces_plan_presets(agent_client):
    response = agent_client.post(
        "/v1/run",
        json={"url": "https://example.com/video", "preset": "1080p"},
        headers={"X-Agent-Key": "k" * 40},
    )
    assert response.status_code == 403


def test_jobs_are_scoped_to_the_paired_user(agent_client):
    response = agent_client.get("/v1/jobs/doesnotexist", headers={"X-Agent-Key": "k" * 40})
    assert response.status_code == 404


def test_run_is_blocked_when_server_quota_is_exhausted(agent_client, monkeypatch):
    from mydl_desktop import metering

    def deny(config, **kwargs):
        raise metering.QuotaExceeded("Hourly limit reached (20/20 this hour).", 600)

    monkeypatch.setattr(metering, "reserve", deny)
    response = agent_client.post(
        "/v1/run",
        json={"url": "https://example.com/video", "preset": "best", "job_id": "quota-job-1"},
        headers={"X-Agent-Key": "k" * 40},
    )
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "600"
    assert "Hourly limit" in response.json()["detail"]


def test_run_requires_re_pairing_when_the_device_token_is_revoked(agent_client, monkeypatch):
    from mydl_desktop import metering

    def revoked(config, **kwargs):
        raise metering.PairingLost("The desktop app is no longer paired.")

    monkeypatch.setattr(metering, "reserve", revoked)
    response = agent_client.post(
        "/v1/run",
        json={"url": "https://example.com/video", "preset": "best", "job_id": "revoked-job-1"},
        headers={"X-Agent-Key": "k" * 40},
    )
    assert response.status_code == 401
    assert "no longer paired" in response.json()["detail"]


def test_run_queues_usage_when_the_server_is_unreachable(agent_client, monkeypatch):
    from mydl_desktop import jobs, metering

    def offline(config, **kwargs):
        raise metering.Unavailable("connection refused")

    queued: dict = {}

    def fake_submit(self, **kwargs):
        return jobs.LocalJob(
            id=kwargs["job_id"],
            url=kwargs["url"],
            preset=kwargs["preset"],
            user_id="u1",
            user_email="desk@example.com",
            download_dir=str(self.config.download_path),
        )

    monkeypatch.setattr(metering, "reserve", offline)
    monkeypatch.setattr(metering, "queue_reserve", lambda config, **kw: queued.update(kw))
    monkeypatch.setattr(jobs.JobManager, "submit", fake_submit)

    response = agent_client.post(
        "/v1/run",
        json={"url": "https://example.com/video", "preset": "best", "job_id": "offline-job-1"},
        headers={"X-Agent-Key": "k" * 40},
    )
    assert response.status_code == 201, response.text
    assert queued["job_id"] == "offline-job-1"
