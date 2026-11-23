import pytest

from backend.config import settings
from backend.models import User
from backend.plans import plan_for, preset_allowed

VIDEO_URL = "https://example.com/clip.mp4"


@pytest.fixture
def demo(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "demo_max_preset", "480p")
    monkeypatch.setattr(settings, "demo_max_filesize_mb", 200)
    monkeypatch.setattr(settings, "demo_jobs_per_hour", 4)
    monkeypatch.setattr(settings, "demo_max_active_jobs", 1)


def test_demo_mode_caps_every_plan(demo):
    user = User(id="u1", email="u@example.com", password_hash="x", plan="pro")

    for plan in (plan_for(None), plan_for(user)):
        assert plan.max_preset == "480p"
        assert plan.max_filesize_mb == 200
        assert plan.jobs_per_hour <= 4
        assert plan.max_active_jobs == 1
        assert preset_allowed(plan, "480p")
        assert not preset_allowed(plan, "720p")


def test_demo_disables_billing(demo, monkeypatch):
    monkeypatch.setattr(settings, "paddle_api_key", "key")
    monkeypatch.setattr(settings, "paddle_client_token", "token")
    monkeypatch.setattr(settings, "paddle_webhook_secret", "secret")
    monkeypatch.setattr(settings, "paddle_price_pro_monthly", "pri_123")

    assert not settings.billing_enabled


def test_config_reports_demo_limits(demo, client):
    body = client.get("/v1/config").json()

    assert body["demo"]["enabled"] is True
    assert body["demo"]["max_preset"] == "480p"
    assert body["limits"]["max_filesize_mb"] == 200
    assert body["billing"]["enabled"] is False


def test_demo_rejects_premium_presets_and_cookies(demo, client):
    premium = client.post("/v1/jobs", json={"url": VIDEO_URL, "preset": "720p"})
    assert premium.status_code == 403

    cookies = client.post(
        "/v1/jobs",
        json={"url": VIDEO_URL, "preset": "480p", "cookies": "# Netscape HTTP Cookie File"},
    )
    assert cookies.status_code == 403
    assert "cookies" in cookies.json()["detail"].lower()
