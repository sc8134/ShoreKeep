from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlmodel import Session

from ..agent_auth import claim_pair, create_pair, resolve_device
from ..config import settings
from ..db import get_session, quota_guard
from ..models import AgentDevice, DesktopJob, User, utcnow
from ..plans import allowed_presets, plan_for, preset_allowed, usage as plan_usage
from ..ratelimit import SlidingWindowLimiter
from ..security import UnsafeURLError, assert_public_http_url
from .auth import UsageRead, UserRead, usage_read
from .deps import client_ip, ensure_same_origin, require_user

router = APIRouter(prefix="/v1/agent", tags=["agent"])

pair_limiter = SlidingWindowLimiter(10, 3600)
claim_limiter = SlidingWindowLimiter(30, 15 * 60)

PRESETS = ("best", "1080p", "720p", "480p", "audio")
JOB_ID_PATTERN = r"^[A-Za-z0-9_-]{8,64}$"
JOB_ID_RE = re.compile(JOB_ID_PATTERN)


class PairRead(BaseModel):
    pair_token: str
    expires_at: datetime
    claim_url: str


class ClaimRequest(BaseModel):
    pair_token: str = Field(min_length=20, max_length=128)
    label: str | None = Field(default=None, max_length=64)


class AgentLimits(BaseModel):
    plan: str
    max_preset: str | None
    presets: list[str]


class ClaimRead(BaseModel):
    device_token: str
    api_base: str
    user: UserRead
    limits: AgentLimits


class DeviceRead(BaseModel):
    id: str
    label: str | None
    user: UserRead
    limits: AgentLimits
    usage: UsageRead
    created_at: datetime


class DesktopStart(BaseModel):
    job_id: str = Field(pattern=JOB_ID_PATTERN)
    url: str = Field(min_length=10, max_length=2048)
    preset: str = "best"


class DesktopReport(BaseModel):
    status: Literal["done", "failed"]
    preset: str = "best"
    url: str | None = Field(default=None, max_length=2048)
    size_bytes: int = Field(default=0, ge=0, le=1 << 50)
    error: str | None = Field(default=None, max_length=500)
    started_at: datetime | None = None


class DesktopJobRead(BaseModel):
    id: str
    status: str
    usage: UsageRead


def _limits(user: User) -> AgentLimits:
    plan = plan_for(user)
    return AgentLimits(
        plan=plan.name,
        max_preset=plan.max_preset,
        presets=list(allowed_presets(plan)),
    )


def _device(
    authorization: str = Header(default=""),
    session: Session = Depends(get_session),
) -> tuple[AgentDevice, User]:
    token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else None
    resolved = resolve_device(session, token)
    if resolved is None:
        raise HTTPException(
            status_code=401,
            detail="Unknown or revoked device token. Re-pair the desktop app.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return resolved


def _desktop_key(device: AgentDevice, job_id: str) -> str:
    return f"{device.id}:{job_id}"


def _check_preset(plan, preset: str) -> None:
    if preset not in PRESETS:
        raise HTTPException(status_code=400, detail=f"preset must be one of {sorted(PRESETS)}")
    if not preset_allowed(plan, preset):
        raise HTTPException(
            status_code=403,
            detail=f"Your plan allows: {', '.join(allowed_presets(plan))}",
        )


def _clamp_started(value: datetime | None, now: datetime) -> datetime:
    """Keep replayed, offline-reported usage inside a sane window."""
    if value is None:
        return now
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    if value > now:
        return now
    earliest = now - timedelta(hours=1)
    return value if value > earliest else earliest


def _host(url: str | None) -> str | None:
    if not url:
        return None
    return urlparse(url).netloc[:255] or None


def _desktop_read(session: Session, user: User, row: DesktopJob) -> DesktopJobRead:
    return DesktopJobRead(
        id=row.job_id,
        status=row.status,
        usage=usage_read(plan_usage(session, user, None)),
    )


@router.post("/pair", response_model=PairRead, status_code=201)
def create_pairing(
    request: Request,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> PairRead:
    ensure_same_origin(request)
    allowed, retry_after = pair_limiter.allow(user.id)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many app pairing requests. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )
    token, pair = create_pair(session, user)
    return PairRead(
        pair_token=token,
        expires_at=pair.expires_at,
        claim_url=f"{settings.public_base_url.rstrip('/')}/v1/agent/pair/claim",
    )


@router.post("/pair/claim", response_model=ClaimRead)
def claim_pairing(
    payload: ClaimRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> ClaimRead:
    allowed, retry_after = claim_limiter.allow(client_ip(request))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many pairing attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )
    result = claim_pair(session, payload.pair_token.strip(), payload.label)
    if result is None:
        raise HTTPException(
            status_code=400,
            detail="This pairing link is expired or already used. Click “Download with app” again.",
        )
    device_token, device, user = result
    return ClaimRead(
        device_token=device_token,
        api_base=settings.public_base_url.rstrip("/"),
        user=UserRead.from_user(user),
        limits=_limits(user),
    )


@router.get("/me", response_model=DeviceRead)
def device_me(
    device_user: tuple[AgentDevice, User] = Depends(_device),
    session: Session = Depends(get_session),
) -> DeviceRead:
    device, user = device_user
    return DeviceRead(
        id=device.id,
        label=device.label,
        user=UserRead.from_user(user),
        limits=_limits(user),
        usage=usage_read(plan_usage(session, user, None)),
        created_at=device.created_at,
    )


@router.post("/jobs", response_model=DesktopJobRead, status_code=201)
def start_desktop_job(
    payload: DesktopStart,
    response: Response,
    device_user: tuple[AgentDevice, User] = Depends(_device),
    session: Session = Depends(get_session),
) -> DesktopJobRead:
    """Reserve quota for one desktop download before it starts locally."""
    device, user = device_user
    plan = plan_for(user)
    _check_preset(plan, payload.preset)

    url = payload.url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Enter a valid http(s) URL")
    try:
        assert_public_http_url(url)
    except UnsafeURLError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    existing = session.get(DesktopJob, _desktop_key(device, payload.job_id))
    if existing is not None:
        response.status_code = 200
        return _desktop_read(session, user, existing)

    with quota_guard(session, f"user:{user.id}"):
        used = plan_usage(session, user, None)
        if used.used_this_hour >= used.limit_per_hour:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Hourly limit reached ({used.used_this_hour}/{used.limit_per_hour} this hour, "
                    "including downloads on this computer). Try again later."
                ),
                headers={"Retry-After": "600"},
            )
        if used.active_jobs >= used.max_active_jobs:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"You already have {used.active_jobs} download(s) in progress "
                    f"(max {used.max_active_jobs}). Wait for one to finish."
                ),
                headers={"Retry-After": "30"},
            )

        row = DesktopJob(
            id=_desktop_key(device, payload.job_id),
            job_id=payload.job_id,
            user_id=user.id,
            device_id=device.id,
            preset=payload.preset,
            status="running",
            host=_host(url),
        )
        session.add(row)
        session.commit()
    return _desktop_read(session, user, row)


@router.put("/jobs/{job_id}", response_model=DesktopJobRead)
def report_desktop_job(
    job_id: str,
    payload: DesktopReport,
    device_user: tuple[AgentDevice, User] = Depends(_device),
    session: Session = Depends(get_session),
) -> DesktopJobRead:
    """Record the final state of a desktop download (upsert, so it works offline)."""
    device, user = device_user
    if not JOB_ID_RE.match(job_id):
        raise HTTPException(status_code=400, detail="Invalid job id")
    if payload.preset not in PRESETS:
        raise HTTPException(status_code=400, detail=f"preset must be one of {sorted(PRESETS)}")
    if payload.url:
        parsed = urlparse(payload.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(status_code=400, detail="Enter a valid http(s) URL")

    now = utcnow()
    key = _desktop_key(device, job_id)
    row = session.get(DesktopJob, key)
    if row is None:
        row = DesktopJob(
            id=key,
            job_id=job_id,
            user_id=user.id,
            device_id=device.id,
            preset=payload.preset,
            status=payload.status,
            size_bytes=payload.size_bytes,
            host=_host(payload.url),
            error=payload.error,
            created_at=_clamp_started(payload.started_at, now),
            finished_at=now,
        )
        session.add(row)
    elif row.status in {"done", "failed"}:
        return _desktop_read(session, user, row)
    else:
        row.status = payload.status
        row.size_bytes = payload.size_bytes
        row.error = payload.error
        row.finished_at = now
        session.add(row)

    session.commit()
    return _desktop_read(session, user, row)
