"""Paddle Billing integration: checkout, customer portal, webhooks, plan sync.

Paddle is the merchant of record: it collects card payments, handles tax, and
pays out internationally, which matters for sellers outside Stripe-supported
countries. Shorekeep only mirrors subscription state into ``user.plan``.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlmodel import Session, select

from .config import settings
from .models import BillingEvent, Subscription, User, utcnow

logger = logging.getLogger(__name__)

PRO_STATUSES = {"active", "trialing", "past_due"}
WEBHOOK_TOLERANCE_SECONDS = 5
_UNAVAILABLE = "Payments are unavailable right now. Please try again shortly."


class BillingError(RuntimeError):
    """Billing is unavailable or misconfigured."""


class BillingNotConfigured(BillingError):
    pass


class BillingProviderError(BillingError):
    pass


def price_id_for(interval: str) -> str | None:
    if interval == "year":
        return settings.paddle_price_pro_yearly or None
    return settings.paddle_price_pro_monthly or None


def price_label(interval: str) -> str:
    amount = settings.pro_price_yearly_usd if interval == "year" else settings.pro_price_monthly_usd
    return f"${amount}"


def _request(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if not settings.paddle_api_key:
        raise BillingNotConfigured("Billing is not configured")
    try:
        response = httpx.request(
            method,
            f"{settings.paddle_api_base}{path}",
            headers={
                "Authorization": f"Bearer {settings.paddle_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15,
        )
    except httpx.HTTPError as exc:
        logger.warning("Paddle request %s %s failed: %s", method, path, exc)
        raise BillingProviderError(_UNAVAILABLE) from exc
    if response.status_code >= 400:
        logger.warning("Paddle request %s %s returned HTTP %s", method, path, response.status_code)
        raise BillingProviderError(_UNAVAILABLE)
    return response.json()


def create_checkout_transaction(user: User, interval: str) -> str:
    price_id = price_id_for(interval)
    if price_id is None:
        raise BillingNotConfigured("Pro pricing is not configured")
    body = _request(
        "POST",
        "/transactions",
        {
            "items": [{"price_id": price_id, "quantity": 1}],
            "custom_data": {"user_id": user.id},
        },
    )
    transaction_id = (body.get("data") or {}).get("id")
    if not isinstance(transaction_id, str):
        raise BillingProviderError(_UNAVAILABLE)
    return transaction_id


def create_portal_url(subscription: Subscription) -> str:
    if not subscription.customer_id:
        raise BillingProviderError("No billing customer on file yet")
    payload: dict[str, Any] = {}
    if subscription.subscription_id:
        payload["subscription_ids"] = [subscription.subscription_id]
    body = _request("POST", f"/customers/{subscription.customer_id}/portal-sessions", payload)
    urls = (body.get("data") or {}).get("urls") or {}
    url = (urls.get("general") or {}).get("overview")
    if not isinstance(url, str) or not url:
        raise BillingProviderError("The billing portal is unavailable right now.")
    return url


def verify_webhook_signature(raw_body: bytes, header: str | None, secret: str | None = None) -> bool:
    secret = secret if secret is not None else settings.paddle_webhook_secret
    if not secret or not header:
        return False
    parts: dict[str, str] = {}
    for chunk in header.split(";"):
        key, _, value = chunk.partition("=")
        if key.strip():
            parts[key.strip()] = value.strip()
    timestamp, digest = parts.get("ts"), parts.get("h1")
    if not timestamp or not digest:
        return False
    try:
        sent_at = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - sent_at) > WEBHOOK_TOLERANCE_SECONDS:
        return False
    expected = hmac.new(
        secret.encode(), timestamp.encode() + b":" + raw_body, hashlib.sha256
    ).hexdigest()
    return any(hmac.compare_digest(expected, candidate) for candidate in digest.split(","))


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _interval_from(data: dict[str, Any]) -> str | None:
    cycle = data.get("billing_cycle") or {}
    interval = cycle.get("interval")
    if interval in {"month", "year"}:
        return interval
    items = data.get("items") or []
    price_id = None
    if items and isinstance(items[0], dict):
        price_id = (items[0].get("price") or {}).get("id")
    if price_id and price_id == settings.paddle_price_pro_yearly:
        return "year"
    if price_id and price_id == settings.paddle_price_pro_monthly:
        return "month"
    return None


def _apply_subscription(session: Session, data: dict[str, Any], user_id: str | None) -> str | None:
    subscription_id = data.get("id")
    if not isinstance(subscription_id, str) or not user_id:
        return None
    user = session.get(User, user_id)
    if user is None:
        return None

    subscription = session.get(Subscription, user_id)
    if subscription is None:
        subscription = Subscription(user_id=user_id)

    subscription.provider = "paddle"
    subscription.subscription_id = subscription_id
    if data.get("customer_id"):
        subscription.customer_id = str(data["customer_id"])
    items = data.get("items") or []
    if items and isinstance(items[0], dict):
        price_id = (items[0].get("price") or {}).get("id")
        if price_id:
            subscription.price_id = str(price_id)
    subscription.interval = _interval_from(data) or subscription.interval
    status = data.get("status")
    if isinstance(status, str) and status:
        subscription.status = status.lower()
    period = data.get("current_billing_period") or {}
    ends_at = _parse_dt(period.get("ends_at"))
    if ends_at is not None:
        subscription.current_period_end = ends_at
    scheduled = data.get("scheduled_change") or {}
    action = scheduled.get("action")
    if action:
        subscription.cancel_at_period_end = action == "cancel"
    subscription.updated_at = utcnow()

    session.add(subscription)
    user.plan = "pro" if subscription.status in PRO_STATUSES else "free"
    session.add(user)
    return user_id


def handle_webhook(session: Session, event_id: str, event_type: str, data: dict[str, Any]) -> str | None:
    """Apply one verified Paddle event. Returns the affected user id, if any."""
    if session.get(BillingEvent, event_id) is not None:
        return None

    user_id: str | None = None
    custom = data.get("custom_data") or {}
    candidate = custom.get("user_id") if isinstance(custom, dict) else None
    if isinstance(candidate, str):
        user_id = candidate

    if event_type.startswith("subscription."):
        if user_id is None:
            subscription_id = data.get("id")
            if isinstance(subscription_id, str):
                existing = session.exec(
                    select(Subscription).where(Subscription.subscription_id == subscription_id)
                ).first()
                user_id = existing.user_id if existing is not None else None
        user_id = _apply_subscription(session, data, user_id)
    elif event_type == "transaction.completed":
        if isinstance(user_id, str):
            subscription = session.get(Subscription, user_id)
            customer_id = data.get("customer_id")
            if subscription is not None and customer_id and not subscription.customer_id:
                subscription.customer_id = str(customer_id)
                subscription.updated_at = utcnow()
                session.add(subscription)

    session.add(BillingEvent(event_id=event_id, event_type=event_type, user_id=user_id))
    session.commit()
    return user_id


def sync_plan(session: Session, user: User) -> User:
    """Lazy safety net so a missed webhook cannot leave stale Pro access."""
    if user.plan != "pro":
        return user
    subscription = session.get(Subscription, user.id)
    if subscription is None:
        return user
    expired = False
    if subscription.status not in PRO_STATUSES:
        expired = True
    elif subscription.status != "past_due" and subscription.current_period_end is not None:
        period_end = subscription.current_period_end
        if period_end.tzinfo is None:
            period_end = period_end.replace(tzinfo=timezone.utc)
        expired = utcnow() > period_end + timedelta(days=1)
    if expired:
        user.plan = "free"
        session.add(user)
        session.commit()
    return user


def subscription_for(session: Session, user: User) -> Subscription | None:
    return session.get(Subscription, user.id)


def subscription_summary(session: Session, user: User) -> dict[str, Any] | None:
    subscription = session.get(Subscription, user.id)
    if subscription is None:
        return None
    return {
        "status": subscription.status,
        "interval": subscription.interval,
        "current_period_end": subscription.current_period_end,
        "cancel_at_period_end": subscription.cancel_at_period_end,
        "can_manage": bool(subscription.customer_id),
    }
