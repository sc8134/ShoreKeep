from datetime import datetime, timezone

import pytest

pytest.importorskip("mydl_desktop")

from mydl_desktop import metering
from mydl_desktop.config import AgentConfig

URL = "https://example.com/video.mp4"


def _config(tmp_path, token: str | None = "device-token") -> AgentConfig:
    return AgentConfig(device_token=token, state_dir=str(tmp_path))


def test_reserve_requires_pairing(tmp_path):
    with pytest.raises(metering.PairingLost):
        metering.reserve(_config(tmp_path, token=None), job_id="job-abc12345", url=URL, preset="best")


def test_outbox_replays_and_clears(tmp_path, monkeypatch):
    config = _config(tmp_path)
    metering.queue_reserve(config, job_id="job-abc12345", url=URL, preset="720p")
    assert (tmp_path / "outbox.jsonl").exists()

    calls = []

    def fake_request(cfg, method, path, payload):
        calls.append((method, path, payload))
        return {}

    monkeypatch.setattr(metering, "_request", fake_request)
    assert metering.flush(config) == 1
    assert calls[0][0] == "POST"
    assert calls[0][1] == "/v1/agent/jobs"
    assert calls[0][2]["job_id"] == "job-abc12345"
    assert not (tmp_path / "outbox.jsonl").exists()


def test_outbox_is_kept_while_offline(tmp_path, monkeypatch):
    config = _config(tmp_path)
    metering.queue_reserve(config, job_id="job-abc12345", url=URL, preset="720p")

    def offline(cfg, method, path, payload):
        raise metering.Unavailable("connection refused")

    monkeypatch.setattr(metering, "_request", offline)
    assert metering.flush(config) == 0
    assert (tmp_path / "outbox.jsonl").exists()


def test_complete_queues_on_failure_instead_of_raising(tmp_path, monkeypatch):
    config = _config(tmp_path)

    def offline(cfg, method, path, payload):
        raise metering.Unavailable("connection refused")

    monkeypatch.setattr(metering, "_request", offline)
    metering.complete(
        config,
        job_id="job-abc12345",
        status="done",
        preset="best",
        url=URL,
        size_bytes=10,
        error=None,
        started_at=datetime.now(timezone.utc),
    )
    events = (tmp_path / "outbox.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(events) == 1
    assert '"kind":"complete"' in events[0]


def test_rejected_usage_is_dropped_not_looped(tmp_path, monkeypatch):
    config = _config(tmp_path)
    metering.queue_reserve(config, job_id="job-abc12345", url=URL, preset="720p")

    def reject(cfg, method, path, payload):
        raise metering.Rejected("preset must be one of ...")

    monkeypatch.setattr(metering, "_request", reject)
    assert metering.flush(config) == 1
    assert not (tmp_path / "outbox.jsonl").exists()
