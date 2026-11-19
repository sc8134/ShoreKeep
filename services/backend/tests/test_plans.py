from backend.auth import hash_ip
from backend.models import User
from backend.plans import allowed_presets, plan_for, preset_allowed


def test_anonymous_plan_caps_presets(monkeypatch):
    from backend.config import settings

    monkeypatch.setattr(settings, "anon_jobs_per_ip_hour", 3)
    monkeypatch.setattr(settings, "anon_max_preset", "720p")
    plan = plan_for(None)
    assert plan.name == "anonymous"
    assert plan.jobs_per_hour == 3
    assert preset_allowed(plan, "480p")
    assert preset_allowed(plan, "720p")
    assert preset_allowed(plan, "audio")
    assert not preset_allowed(plan, "1080p")
    assert not preset_allowed(plan, "best")
    assert allowed_presets(plan) == ("720p", "480p", "audio")


def test_anonymous_cap_can_be_disabled(monkeypatch):
    from backend.config import settings

    monkeypatch.setattr(settings, "anon_max_preset", "")
    plan = plan_for(None)
    assert plan.max_preset is None
    assert preset_allowed(plan, "best")


def test_free_user_gets_all_presets():
    user = User(id="u1", email="u@example.com", password_hash="x")
    plan = plan_for(user)
    assert plan.name == "free"
    assert plan.max_preset is None
    assert preset_allowed(plan, "best")


def test_ip_hash_is_stable_and_pseudonymous():
    assert hash_ip("203.0.113.7") == hash_ip("203.0.113.7")
    assert hash_ip("203.0.113.7") != hash_ip("203.0.113.8")
    assert hash_ip(None) is None
    assert "203.0.113" not in hash_ip("203.0.113.7")
