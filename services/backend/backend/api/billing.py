from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session

from .. import billing
from ..config import settings
from ..db import get_session
from ..models import User
from ..ratelimit import SlidingWindowLimiter
from .deps import ensure_same_origin, require_user

router = APIRouter(prefix="/v1/billing", tags=["billing"])

checkout_limiter = SlidingWindowLimiter(10, 15 * 60)


class CheckoutRequest(BaseModel):
    interval: Literal["month", "year"] = "month"


class CheckoutRead(BaseModel):
    transaction_id: str


class PortalRead(BaseModel):
    url: str


def _billing_error(exc: billing.BillingError) -> HTTPException:
    status_code = 503 if isinstance(exc, billing.BillingNotConfigured) else 502
    return HTTPException(status_code=status_code, detail=str(exc))


@router.post("/checkout", response_model=CheckoutRead)
def checkout(
    payload: CheckoutRequest,
    request: Request,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> CheckoutRead:
    ensure_same_origin(request)
    if not settings.billing_enabled:
        raise HTTPException(status_code=503, detail="Billing is not configured")
    if user.plan == "pro":
        raise HTTPException(status_code=409, detail="You are already on Pro")
    subscription = billing.subscription_for(session, user)
    if subscription is not None and subscription.status in billing.PRO_STATUSES:
        raise HTTPException(status_code=409, detail="You already have an active subscription")
    allowed, retry_after = checkout_limiter.allow(f"user:{user.id}")
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many checkout attempts. Try again shortly.",
            headers={"Retry-After": str(retry_after)},
        )
    try:
        transaction_id = billing.create_checkout_transaction(user, payload.interval)
    except billing.BillingError as exc:
        raise _billing_error(exc) from exc
    return CheckoutRead(transaction_id=transaction_id)


@router.post("/portal", response_model=PortalRead)
def portal(
    request: Request,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> PortalRead:
    ensure_same_origin(request)
    if not settings.billing_enabled:
        raise HTTPException(status_code=503, detail="Billing is not configured")
    subscription = billing.subscription_for(session, user)
    if subscription is None or not subscription.customer_id:
        raise HTTPException(status_code=404, detail="No billing account on file")
    try:
        url = billing.create_portal_url(subscription)
    except billing.BillingError as exc:
        raise _billing_error(exc) from exc
    return PortalRead(url=url)


@router.post("/webhook")
async def webhook(
    request: Request,
    session: Session = Depends(get_session),
) -> dict[str, bool]:
    if not settings.paddle_webhook_secret:
        raise HTTPException(status_code=503, detail="Billing is not configured")
    raw = await request.body()
    if not billing.verify_webhook_signature(raw, request.headers.get("Paddle-Signature")):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook payload") from exc
    event_id = payload.get("event_id")
    event_type = payload.get("event_type")
    data = payload.get("data")
    if not isinstance(event_id, str) or not isinstance(event_type, str) or not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Invalid webhook payload")
    try:
        billing.handle_webhook(session, event_id, event_type, data)
    except Exception as exc:  # pragma: no cover - defensive: let Paddle retry
        session.rollback()
        raise HTTPException(status_code=500, detail="Could not process webhook") from exc
    return {"ok": True}
