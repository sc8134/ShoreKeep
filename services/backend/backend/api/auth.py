from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from .. import billing
from ..auth import (
    SESSION_COOKIE,
    AuthError,
    burn_password_check,
    consume_reset_token,
    create_session,
    generate_reset_token,
    generate_verify_token,
    get_user_by_email,
    hash_password,
    normalize_email,
    purge_expired_sessions,
    revoke_session,
    validate_email,
    validate_password,
    verify_email_token,
    verify_password,
    verify_reset_token,
)
from ..config import settings
from ..db import get_session
from ..email import send_password_reset_email, send_verification_email
from ..models import User
from ..plans import Usage, usage as plan_usage
from ..ratelimit import SlidingWindowLimiter
from .deps import client_ip, ensure_same_origin, optional_user, require_user

router = APIRouter(prefix="/v1/auth", tags=["auth"])

register_limiter = SlidingWindowLimiter(10, 15 * 60)
login_limiter = SlidingWindowLimiter(20, 15 * 60)


class Credentials(BaseModel):
    email: str
    password: str


class UserRead(BaseModel):
    id: str
    email: str
    plan: str
    email_verified: bool = False
    created_at: datetime

    @classmethod
    def from_user(cls, user: User) -> "UserRead":
        return cls(
            id=user.id,
            email=user.email,
            plan=user.plan,
            email_verified=bool(getattr(user, "email_verified", False)),
            created_at=user.created_at,
        )


class UsageRead(BaseModel):
    used_this_hour: int
    limit_per_hour: int
    active_jobs: int
    max_active_jobs: int
    max_preset: str | None
    presets: list[str]


class SubscriptionRead(BaseModel):
    status: str
    interval: str | None
    current_period_end: datetime | None
    cancel_at_period_end: bool
    can_manage: bool


class MeRead(BaseModel):
    user: UserRead
    usage: UsageRead
    subscription: SubscriptionRead | None = None


def usage_read(usage: Usage) -> UsageRead:
    return UsageRead(
        used_this_hour=usage.used_this_hour,
        limit_per_hour=usage.limit_per_hour,
        active_jobs=usage.active_jobs,
        max_active_jobs=usage.max_active_jobs,
        max_preset=usage.max_preset,
        presets=list(usage.presets),
    )


def me_read(session: Session, user: User) -> MeRead:
    summary = billing.subscription_summary(session, user)
    return MeRead(
        user=UserRead.from_user(user),
        usage=usage_read(plan_usage(session, user, None)),
        subscription=SubscriptionRead(**summary) if summary else None,
    )


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_days * 86400,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


def _rate_limit(limiter: SlidingWindowLimiter, key: str, message: str) -> None:
    allowed, retry_after = limiter.allow(key)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=message,
            headers={"Retry-After": str(retry_after)},
        )


@router.post("/register", response_model=MeRead, status_code=201)
def register(
    payload: Credentials,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
) -> MeRead:
    ip = client_ip(request)
    _rate_limit(register_limiter, ip, "Too many sign-up attempts. Try again later.")

    email = normalize_email(payload.email)
    try:
        validate_email(email)
        validate_password(payload.password)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if get_user_by_email(session, email) is not None:
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    user = User(email=email, password_hash=hash_password(payload.password))
    session.add(user)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="An account with this email already exists") from exc
    session.refresh(user)

    purge_expired_sessions(session)
    token = create_session(session, user, ip, request.headers.get("user-agent"))
    _set_session_cookie(response, token)

    # send verification email — non-blocking, never fails the request
    try:
        verify_token = generate_verify_token(session, user)
        send_verification_email(user.email, verify_token)
    except Exception:
        pass  # logged inside send_verification_email

    return me_read(session, user)


@router.post("/login", response_model=MeRead)
def login(
    payload: Credentials,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
) -> MeRead:
    ip = client_ip(request)
    _rate_limit(login_limiter, ip, "Too many sign-in attempts. Try again later.")

    email = normalize_email(payload.email)
    user = get_user_by_email(session, email)
    if user is None:
        burn_password_check(payload.password)
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if user.status != "active" or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    purge_expired_sessions(session)
    token = create_session(session, user, ip, request.headers.get("user-agent"))
    _set_session_cookie(response, token)
    return me_read(session, user)


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
) -> None:
    ensure_same_origin(request)
    revoke_session(session, getattr(request.state, "session_token", None))
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me", response_model=MeRead)
def me(
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> MeRead:
    return me_read(session, user)


# ---------------------------------------------------------------------------
# Email verification
# ---------------------------------------------------------------------------

forgot_limiter = SlidingWindowLimiter(5, 15 * 60)


@router.get("/verify")
def verify_email(
    token: str,
    session: Session = Depends(get_session),
) -> dict:
    user = verify_email_token(session, token)
    if user is None:
        raise HTTPException(status_code=400, detail="This verification link is invalid or has expired.")
    return {"verified": True, "email": user.email}


class ForgotRequest(BaseModel):
    email: str


class ResetRequest(BaseModel):
    token: str
    password: str


@router.post("/forgot", status_code=200)
def forgot_password(
    payload: ForgotRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> dict:
    ip = client_ip(request)
    _rate_limit(forgot_limiter, ip, "Too many password-reset requests. Try again later.")

    email = normalize_email(payload.email)
    user = get_user_by_email(session, email)
    # always return 200 — no user enumeration
    if user is not None and user.status == "active":
        try:
            reset_token = generate_reset_token(session, user)
            send_password_reset_email(user.email, reset_token)
        except Exception:
            pass
    return {"detail": "If that email is registered you will receive a reset link shortly."}


@router.post("/reset-password", status_code=200)
def reset_password(
    payload: ResetRequest,
    session: Session = Depends(get_session),
) -> dict:
    try:
        validate_password(payload.password)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    user = verify_reset_token(session, payload.token)
    if user is None:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired.")

    user.password_hash = hash_password(payload.password)
    session.add(user)
    consume_reset_token(session, user)
    return {"detail": "Password updated. You can now sign in."}


def config_payload() -> dict[str, Any]:
    demo_max_filesize = (
        min(settings.max_filesize_mb, settings.demo_max_filesize_mb)
        if settings.demo_mode
        else settings.max_filesize_mb
    )
    return {
        "turnstile": {
            "enabled": bool(settings.turnstile_enabled and settings.turnstile_secret),
            "site_key": settings.turnstile_site_key,
        },
        "demo": {
            "enabled": settings.demo_mode,
            "max_preset": settings.demo_max_preset or None,
            "max_filesize_mb": demo_max_filesize,
            "jobs_per_hour": settings.demo_jobs_per_hour,
        },
        "limits": {
            "anon_max_preset": (
                settings.demo_max_preset
                if settings.demo_mode and settings.demo_max_preset
                else settings.anon_max_preset or None
            ),
            "max_filesize_mb": demo_max_filesize,
            "retention_seconds": settings.retention_seconds,
        },
        "billing": {
            "enabled": settings.billing_enabled,
            "provider": "paddle",
            "environment": "production" if settings.paddle_env == "production" else "sandbox",
            "client_token": settings.paddle_client_token if settings.billing_enabled else "",
            "currency": settings.billing_currency,
            "prices": {
                "month": billing.price_label("month"),
                "year": billing.price_label("year"),
            },
            "available": {
                "month": bool(settings.paddle_price_pro_monthly),
                "year": bool(settings.paddle_price_pro_yearly),
            },
            "pro": {
                "jobs_per_hour": settings.pro_jobs_per_user_hour,
                "max_active_jobs": settings.pro_max_active_jobs,
                "max_filesize_mb": settings.pro_max_filesize_mb,
            },
        },
    }
