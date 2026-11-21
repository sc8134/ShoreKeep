import hashlib
import hmac
import json
import time
from datetime import timedelta

import pytest
from sqlmodel import Session, select

from backend import billing
from backend.db import engine
from backend.models import BillingEvent, Job, Subscription, User, utcnow

SECRET = "test-paddle-webhook-secret"


def configure_billing(monkeypatch) -> None:
    from backend.config import settings

    monkeypatch.setattr(settings, "paddle_api_key", "pdl_test_key")
    monkeypatch.setattr(settings, "paddle_client_token", "test_client_token")
    monkeypatch.setattr(settings, "paddle_webhook_secret", SECRET)
    monkeypatch.setattr(settings, "paddle_price_pro_monthly", "pri_month")
    monkeypatch.setattr(settings, "paddle_price_pro_yearly", "pri_year")


def _sign(raw: bytes, secret: str = SECRET, timestamp: int | None = None) -> str:
    ts = str(timestamp if timestamp is not None else int(time.time()))
    digest = hmac.new(secret.encode(), ts.encode() + b":" + raw, hashlib.sha256).hexdigest()
    return f"ts={ts};h1={digest}"


def _post_event(client, event_id: str, event_type: str, data: dict, **kwargs):
    raw = json.dumps({"event_id": event_id, "event_type": event_type, "data": data}).encode()
    headers = {"Paddle-Signature": _sign(raw, **kwargs), "Content-Type": "application/json"}
    return client.post("/v1/billing/webhook", content=raw, headers=headers)


def _subscription_data(
    user_id: str,
    status: str = "active",
    period_end: str = "2030-01-01T00:00:00Z",
    price_id: str = "pri_month",
    scheduled: dict | None = None,
) -> dict:
    return {
        "id": "sub_test_1",
        "customer_id": "ctm_test_1",
        "status": status,
        "custom_data": {"user_id": user_id},
        "items": [{"price": {"id": price_id}, "quantity": 1}],
        "billing_cycle": {"interval": "month", "frequency": 1},
        "current_billing_period": {"starts_at": "2026-01-01T00:00:00Z", "ends_at": period_end},
        "scheduled_change": scheduled,
    }


def _user(email: str) -> User:
    with Session(engine) as session:
        user = session.exec(select(User).where(User.email == email)).first()
        assert user is not None
        return user


def _set_plan(email: str, plan: str) -> User:
    with Session(engine) as session:
        user = session.exec(select(User).where(User.email == email)).first()
        assert user is not None
        user.plan = plan
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


def test_config_hides_billing_when_unconfigured(client, monkeypatch):
    from backend.config import settings

    monkeypatch.setattr(settings, "paddle_api_key", "")
    monkeypatch.setattr(settings, "paddle_client_token", "")
    monkeypatch.setattr(settings, "paddle_webhook_secret", "")
    body = client.get("/v1/config").json()
    assert body["billing"]["enabled"] is False
    assert body["billing"]["client_token"] == ""


def test_config_exposes_billing_when_configured(client, monkeypatch):
    configure_billing(monkeypatch)
    body = client.get("/v1/config").json()["billing"]
    assert body["enabled"] is True
    assert body["provider"] == "paddle"
    assert body["environment"] == "sandbox"
    assert body["prices"] == {"month": "$5", "year": "$45"}
    assert body["available"] == {"month": True, "year": True}
    assert body["pro"]["max_filesize_mb"] == 4096
    assert body["pro"]["jobs_per_hour"] > 0


def test_billing_requires_a_monthly_price(client, monkeypatch):
    configure_billing(monkeypatch)
    from backend.config import settings

    monkeypatch.setattr(settings, "paddle_price_pro_monthly", "")
    body = client.get("/v1/config").json()["billing"]
    assert body["enabled"] is False
    assert body["available"] == {"month": False, "year": True}


def test_checkout_requires_sign_in(client, monkeypatch):
    configure_billing(monkeypatch)
    response = client.post("/v1/billing/checkout", json={"interval": "month"})
    assert response.status_code == 401


def test_checkout_creates_paddle_transaction(client, register, monkeypatch):
    configure_billing(monkeypatch)
    register("buyer@example.com")
    seen: list[str] = []

    def fake_checkout(user, interval):
        seen.append(interval)
        return "txn_test_123"

    monkeypatch.setattr(billing, "create_checkout_transaction", fake_checkout)
    response = client.post("/v1/billing/checkout", json={"interval": "year"})
    assert response.status_code == 200
    assert response.json()["transaction_id"] == "txn_test_123"
    assert seen == ["year"]


def test_checkout_rejects_existing_pro(client, register, monkeypatch):
    configure_billing(monkeypatch)
    register("pro@example.com")
    _set_plan("pro@example.com", "pro")
    response = client.post("/v1/billing/checkout", json={"interval": "month"})
    assert response.status_code == 409


def test_checkout_reports_provider_failure(client, register, monkeypatch):
    configure_billing(monkeypatch)
    register("fail@example.com")

    def broken(user, interval):
        raise billing.BillingProviderError("Paddle returned HTTP 500")

    monkeypatch.setattr(billing, "create_checkout_transaction", broken)
    response = client.post("/v1/billing/checkout", json={"interval": "month"})
    assert response.status_code == 502


def test_webhook_rejects_bad_and_stale_signatures(client, register, monkeypatch):
    configure_billing(monkeypatch)
    register("hook@example.com")
    user = _user("hook@example.com")
    data = _subscription_data(user.id)

    bad = _post_event(client, "evt_1", "subscription.created", data, secret="wrong-secret")
    assert bad.status_code == 400

    raw = json.dumps({"event_id": "evt_2", "event_type": "subscription.created", "data": data}).encode()
    stale_header = _sign(raw, timestamp=int(time.time()) - 60)
    stale = client.post(
        "/v1/billing/webhook",
        content=raw,
        headers={"Paddle-Signature": stale_header, "Content-Type": "application/json"},
    )
    assert stale.status_code == 400

    assert client.get("/v1/auth/me").json()["user"]["plan"] == "free"


def test_webhook_activates_pro_and_cancel_downgrades(client, register, monkeypatch):
    configure_billing(monkeypatch)
    register("member@example.com")
    user = _user("member@example.com")

    created = _post_event(client, "evt_a", "subscription.created", _subscription_data(user.id))
    assert created.status_code == 200
    me = client.get("/v1/auth/me").json()
    assert me["user"]["plan"] == "pro"
    assert me["subscription"]["status"] == "active"
    assert me["subscription"]["interval"] == "month"
    assert me["subscription"]["can_manage"] is True
    assert me["subscription"]["cancel_at_period_end"] is False

    scheduled = _subscription_data(user.id, scheduled={"action": "cancel", "effective_at": "2030-01-01T00:00:00Z"})
    updated = _post_event(client, "evt_b", "subscription.updated", scheduled)
    assert updated.status_code == 200
    me = client.get("/v1/auth/me").json()
    assert me["user"]["plan"] == "pro"
    assert me["subscription"]["cancel_at_period_end"] is True

    canceled = _post_event(client, "evt_c", "subscription.canceled", _subscription_data(user.id, status="canceled"))
    assert canceled.status_code == 200
    me = client.get("/v1/auth/me").json()
    assert me["user"]["plan"] == "free"
    assert me["subscription"]["status"] == "canceled"


def test_webhook_is_idempotent(client, register, monkeypatch):
    configure_billing(monkeypatch)
    register("dup@example.com")
    user = _user("dup@example.com")
    data = _subscription_data(user.id)

    assert _post_event(client, "evt_same", "subscription.created", data).status_code == 200
    assert _post_event(client, "evt_same", "subscription.created", data).status_code == 200
    with Session(engine) as session:
        events = session.exec(select(BillingEvent).where(BillingEvent.event_id == "evt_same")).all()
        assert len(events) == 1


def test_expired_subscription_downgrades_on_me(client, register, monkeypatch):
    configure_billing(monkeypatch)
    register("expired@example.com")
    user = _user("expired@example.com")
    with Session(engine) as session:
        session.add(
            Subscription(
                user_id=user.id,
                customer_id="ctm_expired",
                subscription_id="sub_expired",
                status="active",
                current_period_end=utcnow() - timedelta(days=2),
            )
        )
        stored = session.get(User, user.id)
        stored.plan = "pro"
        session.add(stored)
        session.commit()

    me = client.get("/v1/auth/me").json()
    assert me["user"]["plan"] == "free"


def test_pro_job_records_larger_file_cap(client, register, monkeypatch):
    from backend.config import settings

    configure_billing(monkeypatch)
    monkeypatch.setattr(settings, "max_filesize_mb", 2048)
    monkeypatch.setattr(settings, "pro_max_filesize_mb", 4096)
    register("big@example.com")
    _set_plan("big@example.com", "pro")

    response = client.post("/v1/jobs", json={"url": "https://example.com/clip.mp4", "preset": "best"})
    assert response.status_code == 201
    with Session(engine) as session:
        job = session.get(Job, response.json()["id"])
        assert job is not None
        assert job.max_filesize_mb == 4096


def test_reporter_uses_job_specific_size_cap(monkeypatch):
    from backend.config import settings
    from backend.engine.pipeline import Reporter
    from backend.engine.types import SizeLimitExceeded

    monkeypatch.setattr(settings, "max_filesize_mb", 2048)
    free = Reporter("free-job")
    with pytest.raises(SizeLimitExceeded):
        free._enforce_limits(3 * 1024 ** 3, 3 * 1024 ** 3)

    pro = Reporter("pro-job", 4096 * 1024 * 1024)
    pro._enforce_limits(3 * 1024 ** 3, 3 * 1024 ** 3)
    with pytest.raises(SizeLimitExceeded):
        pro._enforce_limits(5 * 1024 ** 3, 5 * 1024 ** 3)


def test_portal_requires_a_billing_customer(client, register, monkeypatch):
    configure_billing(monkeypatch)
    register("noportal@example.com")
    response = client.post("/v1/billing/portal")
    assert response.status_code == 404


def test_portal_returns_paddle_link(client, register, monkeypatch):
    configure_billing(monkeypatch)
    register("portal@example.com")
    user = _user("portal@example.com")
    with Session(engine) as session:
        session.add(
            Subscription(
                user_id=user.id,
                customer_id="ctm_portal",
                subscription_id="sub_portal",
                status="active",
            )
        )
        session.commit()

    seen: list[Subscription] = []

    def fake_portal(subscription):
        seen.append(subscription)
        return "https://paddle.example/portal/abc"

    monkeypatch.setattr(billing, "create_portal_url", fake_portal)
    response = client.post("/v1/billing/portal")
    assert response.status_code == 200
    assert response.json()["url"] == "https://paddle.example/portal/abc"
    assert seen and seen[0].customer_id == "ctm_portal"


def test_verify_webhook_signature_accepts_valid_header():
    raw = b'{"event_id":"evt_x"}'
    assert billing.verify_webhook_signature(raw, _sign(raw), SECRET) is True
    assert billing.verify_webhook_signature(raw, _sign(raw, secret="other"), SECRET) is False
    assert billing.verify_webhook_signature(raw, None, SECRET) is False
