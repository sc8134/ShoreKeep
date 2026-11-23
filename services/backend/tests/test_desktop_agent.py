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
    monkeypatch.setattr("mydl_desktop.config.config_dir", lambda: tmp_path)
    config = AgentConfig(
        download_dir=str(tmp_path / "downloads"),
        state_dir=str(tmp_path / "state"),
        device_token="device-token",
    )
    manager = JobManager(config)
    app = create_app(config, manager)
    with TestClient(app, base_url="http://127.0.0.1:47821") as client:
        client.manager = manager
        client.agent_config = config
        yield client


def test_health_reports_browsers(agent_client):
    response = agent_client.get("/health")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["paused"] is False
    assert body["active"] == 0
    assert any(item["key"] == "firefox" for item in body["browsers"])


def test_pause_requires_tray_control_header(agent_client):
    assert agent_client.post("/v1/pause").status_code == 403
    assert agent_client.post("/v1/resume").status_code == 403


def test_pause_blocks_new_downloads_until_resume(agent_client):
    paused = agent_client.post("/v1/pause", headers={"X-Shorekeep-Control": "tray"})
    assert paused.status_code == 200
    assert paused.json()["paused"] is True
    assert agent_client.get("/health").json()["paused"] is True

    run = agent_client.post(
        "/v1/run",
        json={"url": "https://example.com/video", "preset": "best"},
        headers={"X-Agent-Key": "k" * 40},
    )
    assert run.status_code == 503
    assert "paused" in run.json()["detail"].lower()

    resumed = agent_client.post("/v1/resume", headers={"X-Shorekeep-Control": "tray"})
    assert resumed.status_code == 200
    assert agent_client.get("/health").json()["paused"] is False


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


def test_health_reports_active_jobs_and_last_event(agent_client):
    from mydl_desktop.jobs import LocalJob

    config = agent_client.agent_config
    job = LocalJob(
        id="activejob01",
        url="https://example.com/video",
        preset="best",
        user_id="u1",
        user_email="desk@example.com",
        download_dir=str(config.download_path),
        status="downloading",
        stage="downloading",
        progress=42.5,
        title="An in-flight video",
    )
    agent_client.manager._jobs[job.id] = job

    body = agent_client.get("/health").json()
    assert body["active"] == 1
    assert body["active_jobs"][0]["id"] == "activejob01"
    assert body["active_jobs"][0]["progress"] == 42.5
    assert body["last_event"] is None

    agent_client.manager.activity.record(
        {"id": "finished01", "title": "Done video", "status": "done"}
    )
    event = agent_client.get("/health").json()["last_event"]
    assert event["id"] == "finished01"
    assert event["title"] == "Done video"
    assert event["status"] == "done"
    assert event["finished_at"]


def test_activity_lists_entries_without_output_paths(agent_client):
    store = agent_client.manager.activity
    for index in range(3):
        store.record(
            {
                "id": f"activity{index}",
                "title": f"Video {index}",
                "status": "done",
                "output_path": f"/downloads/video-{index}.mp4",
            }
        )

    response = agent_client.get("/v1/activity?limit=2")
    assert response.status_code == 200
    entries = response.json()
    assert [entry["id"] for entry in entries] == ["activity2", "activity1"]
    assert all("output_path" not in entry for entry in entries)


def test_activity_requires_no_pairing(agent_client):
    assert agent_client.get("/v1/activity").status_code == 200


def test_reveal_requires_local_control(agent_client):
    response = agent_client.post("/v1/activity/reveal", json={"id": "anything"})
    assert response.status_code == 403

    response = agent_client.post(
        "/v1/activity/reveal",
        json={"id": "anything"},
        headers={"X-Shorekeep-Control": "web"},
    )
    assert response.status_code == 403


def test_pause_accepts_the_ui_control_value(agent_client):
    paused = agent_client.post("/v1/pause", headers={"X-Shorekeep-Control": "ui"})
    assert paused.status_code == 200
    assert paused.json()["paused"] is True
    agent_client.post("/v1/resume", headers={"X-Shorekeep-Control": "ui"})


def test_reveal_reports_unknown_and_fileless_downloads(agent_client):
    response = agent_client.post(
        "/v1/activity/reveal",
        json={"id": "missing"},
        headers={"X-Shorekeep-Control": "ui"},
    )
    assert response.status_code == 404

    agent_client.manager.activity.record({"id": "failedone", "status": "failed"})
    response = agent_client.post(
        "/v1/activity/reveal",
        json={"id": "failedone"},
        headers={"X-Shorekeep-Control": "ui"},
    )
    assert response.status_code == 404


def test_reveal_refuses_files_outside_the_download_folder(agent_client, tmp_path, monkeypatch):
    from mydl_desktop import server as server_module

    opened: list = []
    monkeypatch.setattr(server_module, "reveal_path", opened.append)
    outside = tmp_path / "elsewhere.mp4"
    outside.write_bytes(b"content")
    agent_client.manager.activity.record(
        {"id": "outside01", "status": "done", "output_path": str(outside)}
    )

    response = agent_client.post(
        "/v1/activity/reveal",
        json={"id": "outside01"},
        headers={"X-Shorekeep-Control": "ui"},
    )
    assert response.status_code == 403
    assert opened == []


def test_reveal_reports_files_that_were_moved(agent_client, monkeypatch):
    from mydl_desktop import server as server_module

    opened: list = []
    monkeypatch.setattr(server_module, "reveal_path", opened.append)
    target = agent_client.agent_config.download_path / "gone.mp4"
    agent_client.manager.activity.record(
        {"id": "gonejob01", "status": "done", "output_path": str(target)}
    )

    response = agent_client.post(
        "/v1/activity/reveal",
        json={"id": "gonejob01"},
        headers={"X-Shorekeep-Control": "ui"},
    )
    assert response.status_code == 410
    assert opened == []


def test_reveal_opens_files_inside_the_download_folder(agent_client, monkeypatch):
    from mydl_desktop import server as server_module

    opened: list = []
    monkeypatch.setattr(server_module, "reveal_path", opened.append)
    root = agent_client.agent_config.download_path
    root.mkdir(parents=True, exist_ok=True)
    target = root / "clip.mp4"
    target.write_bytes(b"content")
    agent_client.manager.activity.record(
        {"id": "revealjob1", "title": "Clip", "status": "done", "output_path": str(target)}
    )

    response = agent_client.post(
        "/v1/activity/reveal",
        json={"id": "revealjob1"},
        headers={"X-Shorekeep-Control": "ui"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True}
    assert opened == [target]


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


def test_pending_requires_local_control(agent_client):
    assert agent_client.get("/v1/pending").status_code == 403
    clear = agent_client.post("/v1/pending/clear", json={"id": "detected-1"})
    assert clear.status_code == 403


def test_detected_link_flows_through_pending_and_clears(agent_client):
    detected = agent_client.post(
        "/v1/detected",
        json={"url": "https://example.com/watch?v=1", "source": "clipboard"},
        headers={"X-Shorekeep-Control": "tray"},
    )
    assert detected.status_code == 201, detected.text
    entry = detected.json()
    assert entry["id"] == "detected-1"
    assert entry["source"] == "clipboard"

    assert agent_client.get("/health").json()["pending"]["id"] == "detected-1"
    pending = agent_client.get("/v1/pending", headers={"X-Shorekeep-Control": "ui"})
    assert pending.status_code == 200
    assert pending.json()["url"] == "https://example.com/watch?v=1"

    stale = agent_client.post(
        "/v1/pending/clear",
        json={"id": "detected-999"},
        headers={"X-Shorekeep-Control": "ui"},
    )
    assert stale.json() == {"ok": False}

    cleared = agent_client.post(
        "/v1/pending/clear",
        json={"id": "detected-1"},
        headers={"X-Shorekeep-Control": "ui"},
    )
    assert cleared.json() == {"ok": True}
    assert agent_client.get("/v1/pending", headers={"X-Shorekeep-Control": "ui"}).json() is None
    assert agent_client.get("/health").json()["pending"] is None


def test_detected_rejects_non_http_targets(agent_client):
    response = agent_client.post(
        "/v1/detected",
        json={"url": "ftp://example.com/file", "source": "clipboard"},
        headers={"X-Shorekeep-Control": "tray"},
    )
    assert response.status_code == 400


def test_settings_get_defaults_and_update(agent_client, tmp_path):
    settings = agent_client.get("/v1/settings", headers={"X-Shorekeep-Control": "ui"})
    assert settings.status_code == 200
    body = settings.json()
    assert body["clipboard_monitor"] is False
    assert body["watch_folder"] == ""
    assert body["hotkey"] == "ctrl+alt+s"

    folder = tmp_path / "intake"
    updated = agent_client.post(
        "/v1/settings",
        json={"clipboard_monitor": True, "watch_folder": str(folder)},
        headers={"X-Shorekeep-Control": "ui"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["clipboard_monitor"] is True
    assert updated.json()["watch_folder"] == str(folder)
    assert folder.is_dir()

    again = agent_client.get("/v1/settings", headers={"X-Shorekeep-Control": "tray"}).json()
    assert again["clipboard_monitor"] is True
    assert again["watch_folder"] == str(folder)

    saved = AgentConfig.load()
    assert saved.clipboard_monitor is True
    assert saved.watch_folder == str(folder)


def test_settings_reject_a_file_as_the_watch_folder(agent_client, tmp_path):
    target = tmp_path / "notafolder"
    target.write_text("x", encoding="utf-8")
    response = agent_client.post(
        "/v1/settings",
        json={"watch_folder": str(target)},
        headers={"X-Shorekeep-Control": "ui"},
    )
    assert response.status_code == 400


def test_settings_can_turn_detection_off(agent_client, tmp_path):
    agent_client.post(
        "/v1/settings",
        json={"clipboard_monitor": True, "watch_folder": str(tmp_path / "intake")},
        headers={"X-Shorekeep-Control": "ui"},
    )
    response = agent_client.post(
        "/v1/settings",
        json={"clipboard_monitor": False, "watch_folder": ""},
        headers={"X-Shorekeep-Control": "ui"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["clipboard_monitor"] is False
    assert body["watch_folder"] == ""
