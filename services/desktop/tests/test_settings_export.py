"""Phase 8: portable settings export and import."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch


def _make_app(tmp_path: Path):
    """Create a test FastAPI app with a mocked config and manager."""
    from mydl_desktop.config import AgentConfig
    from mydl_desktop.jobs import JobManager
    from mydl_desktop.server import create_app

    config = AgentConfig()
    config.download_dir = str(tmp_path / "downloads")
    config.hotkey = "ctrl+alt+s"
    config.cookie_browser = "auto"
    config.idle_timeout_minutes = 30
    config.clipboard_monitor = False
    config.watch_folder = ""
    config.allowed_origins = ["http://127.0.0.1:8000"]

    # save() should be a no-op in tests
    config.save = MagicMock()

    manager = MagicMock()
    manager.activity = MagicMock()
    manager.activity.latest = MagicMock(return_value=None)
    manager.paused = MagicMock(return_value=False)
    manager.active_count = MagicMock(return_value=0)
    manager.active_jobs = MagicMock(return_value=[])
    manager.touch = MagicMock()
    manager.last_activity = 0

    app = create_app(config, manager)
    return app, config


def _control_client(app):
    from fastapi.testclient import TestClient
    return TestClient(app, headers={
        "host": "127.0.0.1:47821",
        "X-Shorekeep-Control": "ui",
    })


def test_export_settings_returns_expected_shape(tmp_path):
    app, config = _make_app(tmp_path)
    client = _control_client(app)
    r = client.get("/v1/settings/export")
    assert r.status_code == 200
    data = r.json()
    assert "download_dir" in data
    assert "cookie_browser" in data
    assert "hotkey" in data
    assert "clipboard_monitor" in data
    assert "watch_folder" in data
    assert "allowed_origins" in data
    assert "version" in data
    # device_token must NOT be in the export
    assert "device_token" not in data


def test_export_does_not_leak_device_token(tmp_path):
    app, config = _make_app(tmp_path)
    config.device_token = "supersecrettoken"
    client = _control_client(app)
    r = client.get("/v1/settings/export")
    body = r.text
    assert "supersecrettoken" not in body


def test_import_settings_updates_config(tmp_path):
    app, config = _make_app(tmp_path)
    client = _control_client(app)

    new_dl = str(tmp_path / "new_downloads")
    payload = {
        "download_dir": new_dl,
        "cookie_browser": "firefox",
        "idle_timeout_minutes": 60,
        "hotkey": "ctrl+alt+d",
        "clipboard_monitor": True,
        "watch_folder": "",
        "allowed_origins": ["http://127.0.0.1:8000"],
        "version": "0.5.0",
    }
    r = client.post("/v1/settings/import", json=payload)
    assert r.status_code == 200
    assert config.cookie_browser == "firefox"
    assert config.idle_timeout_minutes == 60
    assert config.hotkey == "ctrl+alt+d"
    assert config.clipboard_monitor is True
    config.save.assert_called()


def test_import_invalid_browser_returns_400(tmp_path):
    app, config = _make_app(tmp_path)
    client = _control_client(app)
    r = client.post("/v1/settings/import", json={
        "download_dir": str(tmp_path),
        "cookie_browser": "netscape",
        "idle_timeout_minutes": 30,
        "hotkey": "ctrl+alt+s",
        "clipboard_monitor": False,
        "watch_folder": "",
        "allowed_origins": [],
        "version": "0.5.0",
    })
    assert r.status_code in (400, 422)


def test_import_negative_idle_timeout_returns_400(tmp_path):
    app, config = _make_app(tmp_path)
    client = _control_client(app)
    r = client.post("/v1/settings/import", json={
        "download_dir": str(tmp_path),
        "cookie_browser": "auto",
        "idle_timeout_minutes": -1,
        "hotkey": "ctrl+alt+s",
        "clipboard_monitor": False,
        "watch_folder": "",
        "allowed_origins": [],
        "version": "0.5.0",
    })
    assert r.status_code in (400, 422)


def test_export_requires_control_header(tmp_path):
    app, config = _make_app(tmp_path)
    from fastapi.testclient import TestClient
    client = TestClient(app, headers={"host": "127.0.0.1:47821"})
    r = client.get("/v1/settings/export")
    assert r.status_code == 403


def test_import_requires_control_header(tmp_path):
    app, config = _make_app(tmp_path)
    from fastapi.testclient import TestClient
    client = TestClient(app, headers={"host": "127.0.0.1:47821"})
    r = client.post("/v1/settings/import", json={
        "download_dir": str(tmp_path),
        "cookie_browser": "auto",
        "idle_timeout_minutes": 30,
        "hotkey": "ctrl+alt+s",
        "clipboard_monitor": False,
        "watch_folder": "",
        "allowed_origins": [],
        "version": "0.5.0",
    })
    assert r.status_code == 403
