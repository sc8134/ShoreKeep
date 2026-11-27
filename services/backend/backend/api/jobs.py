from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select as sa_select
from sqlmodel import Session, select

from ..auth import hash_ip
from ..config import settings
from ..cookies import CookieError, encrypt_cookies, validate_cookies
from ..db import engine, get_session, quota_guard
from ..engine.errors import retryable_code
from ..engine.storage import storage
from ..models import ACTIVE_STATUSES, TERMINAL_STATUSES, CookieProfile, Job, User
from ..plans import plan_for, preset_allowed, usage as plan_usage
from ..ratelimit import SlidingWindowLimiter
from ..security import UnsafeURLError, assert_public_http_url
from ..turnstile import TurnstileError, verify as verify_turnstile
from .auth import config_payload
from .deps import client_ip, ensure_same_origin, optional_user, require_user

router = APIRouter(prefix="/v1")

PRESETS = {"best", "1080p", "720p", "480p", "audio"}
anon_limiter = SlidingWindowLimiter(settings.anon_jobs_per_ip_hour, 3600)


class JobCreate(BaseModel):
    url: str = Field(min_length=10, max_length=2048)
    preset: str = "best"
    subtitles: bool = False
    cookies: str | None = None
    cookie_profile_id: str | None = None
    turnstile_token: str | None = None
    priority: int = Field(default=0, ge=0, le=1)
    run_after: datetime | None = None


class JobRead(BaseModel):
    id: str
    url: str
    preset: str
    status: str
    stage: str
    progress: float
    downloaded_bytes: int
    total_bytes: int | None
    speed_bps: float
    eta_seconds: float | None
    title: str | None
    duration: float | None
    thumbnail: str | None
    engine: str | None
    output_name: str | None
    output_size: int | None
    mime: str | None
    subtitle_name: str | None
    subtitle_lang: str | None
    chapters: list[dict] | None
    playlist_index: int | None
    playlist_count: int | None
    error: str | None
    error_code: str | None
    retryable: bool
    created_at: datetime
    download_url: str | None
    subtitle_url: str | None = None
    items_url: str | None = None


def _chapters(job: Job) -> list[dict] | None:
    if not job.chapters:
        return None
    try:
        value = json.loads(job.chapters)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, list) else None


def _to_read(job: Job) -> JobRead:
    done = job.status == "done"
    return JobRead(
        id=job.id,
        url=job.url,
        preset=job.preset,
        status=job.status,
        stage=job.stage,
        progress=job.progress,
        downloaded_bytes=job.downloaded_bytes,
        total_bytes=job.total_bytes,
        speed_bps=job.speed_bps,
        eta_seconds=job.eta_seconds,
        title=job.title,
        duration=job.duration,
        thumbnail=job.thumbnail,
        engine=job.engine,
        output_name=job.output_name,
        output_size=job.output_size,
        mime=job.mime,
        subtitle_name=job.subtitle_name,
        subtitle_lang=job.subtitle_lang,
        chapters=_chapters(job),
        playlist_index=job.playlist_index,
        playlist_count=job.playlist_count,
        error=job.error,
        error_code=job.error_code,
        retryable=job.status == "failed" and retryable_code(job.error_code),
        created_at=job.created_at,
        download_url=f"/v1/jobs/{job.id}/file" if done else None,
        subtitle_url=(
            f"/v1/jobs/{job.id}/subtitles" if done and job.subtitle_path else None
        ),
        items_url=(
            f"/v1/jobs/{job.id}/items"
            if (job.playlist_count and job.playlist_count > 1 and not job.parent_job_id)
            else None
        ),
    )


def _get_visible_job(session: Session, job_id: str, user: User | None) -> Job:
    job = session.get(Job, job_id)
    if job is None or (job.user_id and (user is None or user.id != job.user_id)):
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/config")
def config() -> dict:
    return config_payload()


@router.get("/jobs", response_model=list[JobRead])
def list_jobs(
    limit: int = 20,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> list[JobRead]:
    limit = max(1, min(limit, 50))
    jobs = session.exec(
        select(Job).where(Job.user_id == user.id).order_by(Job.created_at.desc()).limit(limit)
    ).all()
    return [_to_read(job) for job in jobs]


@router.post("/jobs", response_model=JobRead, status_code=201)
def create_job(
    payload: JobCreate,
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
) -> JobRead:
    url = payload.url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Enter a valid http(s) URL")
    if payload.preset not in PRESETS:
        raise HTTPException(status_code=400, detail=f"preset must be one of {sorted(PRESETS)}")

    ensure_same_origin(request)

    plan = plan_for(user)
    if not preset_allowed(plan, payload.preset):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Guests are limited to {plan.max_preset}. "
                "Sign in for free to unlock higher quality."
            ),
        )

    # Priority is Pro-only.
    if payload.priority > 0 and (user is None or plan.name != "pro"):
        raise HTTPException(
            status_code=403,
            detail="Priority scheduling is a Pro feature. Upgrade to move your downloads ahead of the queue.",
        )

    # Validate scheduled time.
    from datetime import timezone as _tz
    run_after = None
    if payload.run_after is not None:
        from datetime import datetime as _dt
        now_dt = _dt.now(_tz.utc)
        ra = payload.run_after
        if ra.tzinfo is None:
            ra = ra.replace(tzinfo=_tz.utc)
        if ra <= now_dt:
            raise HTTPException(status_code=400, detail="Scheduled time must be in the future")
        from datetime import timedelta as _td
        if ra > now_dt + _td(days=7):
            raise HTTPException(status_code=400, detail="Scheduled time can be at most 7 days from now")
        run_after = ra

    ip = client_ip(request)
    client_hash = hash_ip(ip)

    # The DB-backed hourly quota inside quota_guard below is authoritative for
    # signed-in users. The in-process limiter stays as a fast burst guard so
    # floods hit it before any advisory lock or job-count query runs.
    if user is None:
        limiter_key = f"ip:{ip}"
        allowed, retry_after = anon_limiter.allow(limiter_key)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Too many conversion requests. Try again shortly.",
                headers={"Retry-After": str(retry_after)},
            )

    try:
        verify_turnstile(payload.turnstile_token, ip)
    except TurnstileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    try:
        assert_public_http_url(url)
    except UnsafeURLError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    quota_key = f"user:{user.id}" if user else f"ip:{client_hash}"
    with quota_guard(session, quota_key):
        used = plan_usage(session, user, client_hash)
        if used.used_this_hour >= used.limit_per_hour:
            if user is None:
                hint = "Sign in for a higher limit."
            elif plan.name == "free":
                hint = "Upgrade to Pro for 100 per hour."
            else:
                hint = "Try again later."
            raise HTTPException(
                status_code=429,
                detail=f"Hourly limit reached ({plan.jobs_per_hour}/hour). {hint}",
                headers={"Retry-After": "600"},
            )
        if used.active_jobs >= used.max_active_jobs:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"You already have {used.active_jobs} job(s) in progress "
                    f"(max {used.max_active_jobs}). Wait for one to finish."
                ),
                headers={"Retry-After": "30"},
            )

        active = session.scalar(
            sa_select(func.count()).select_from(Job).where(Job.status.in_(ACTIVE_STATUSES))
        )
        if active is not None and active >= settings.max_queue_depth:
            raise HTTPException(
                status_code=429,
                detail="The conversion queue is full right now. Try again shortly.",
                headers={"Retry-After": "30"},
            )

        cookies_enc: str | None = None
        if payload.cookie_profile_id:
            if user is None:
                raise HTTPException(status_code=403, detail="Sign in to use a cookie profile")
            profile = session.get(CookieProfile, payload.cookie_profile_id)
            if profile is None or profile.user_id != user.id:
                raise HTTPException(status_code=404, detail="Cookie profile not found")
            cookies_enc = profile.cookies_enc
        elif payload.cookies and payload.cookies.strip():
            if settings.demo_mode:
                raise HTTPException(
                    status_code=403,
                    detail="The public demo does not accept cookies.",
                )
            if user is None:
                raise HTTPException(
                    status_code=403,
                    detail="Sign in to use your own cookies",
                )
            try:
                cookies_enc = encrypt_cookies(validate_cookies(payload.cookies))
            except CookieError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        job = Job(
            id=uuid.uuid4().hex,
            url=url,
            preset=payload.preset,
            want_subtitles=payload.subtitles,
            user_id=user.id if user else None,
            client_hash=client_hash,
            cookies_enc=cookies_enc,
            max_filesize_mb=plan.max_filesize_mb,
            priority=payload.priority,
            run_after=run_after,
        )
        session.add(job)
        session.commit()

    session.refresh(job)
    return _to_read(job)


@router.get("/jobs/{job_id}", response_model=JobRead)
def get_job(
    job_id: str,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
) -> JobRead:
    return _to_read(_get_visible_job(session, job_id, user))


def _load_payload(job_id: str) -> dict | None:
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if job is None:
            return None
        return _to_read(job).model_dump(mode="json")


@router.get("/jobs/{job_id}/events")
async def job_events(
    job_id: str,
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
) -> StreamingResponse:
    _get_visible_job(session, job_id, user)

    from ..pubsub import IS_REDIS

    async def _redis_stream():
        """Subscribe to the job's Redis channel; fall back to polling if Redis dies."""
        import redis.asyncio as aioredis

        last_payload: str | None = None
        client = aioredis.Redis.from_url(settings.redis_url)
        pubsub = client.pubsub()
        await pubsub.subscribe(f"job:{job_id}")
        try:
            # push the current state first, so late subscribers see something
            payload_obj = await asyncio.to_thread(_load_payload, job_id)
            if payload_obj is None:
                yield 'data: {"error": "Job not found"}\n\n'
                return
            payload = json.dumps(payload_obj)
            if payload != last_payload:
                yield f"data: {payload}\n\n"
                last_payload = payload
            if payload_obj["status"] in TERMINAL_STATUSES:
                return

            while True:
                if await request.is_disconnected():
                    break
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message is None:
                    continue
                if message.get("type") != "message":
                    continue
                try:
                    payload_obj = json.loads(message["data"])
                except (TypeError, ValueError):
                    continue
                if not isinstance(payload_obj, dict):
                    continue
                # the publish payload is a partial; merge with a fresh DB read for full state
                payload_obj = await asyncio.to_thread(_load_payload, job_id) or payload_obj
                payload = json.dumps(payload_obj)
                if payload != last_payload:
                    yield f"data: {payload}\n\n"
                    last_payload = payload
                if payload_obj["status"] in TERMINAL_STATUSES:
                    break
        finally:
            await pubsub.unsubscribe(f"job:{job_id}")
            await client.aclose()

    async def event_stream():
        last_payload: str | None = None
        while True:
            if await request.is_disconnected():
                break
            payload_obj = await asyncio.to_thread(_load_payload, job_id)
            if payload_obj is None:
                yield 'data: {"error": "Job not found"}\n\n'
                break

            payload = json.dumps(payload_obj)
            if payload != last_payload:
                yield f"data: {payload}\n\n"
                last_payload = payload
            if payload_obj["status"] in TERMINAL_STATUSES:
                break
            await asyncio.sleep(0.5)

    if IS_REDIS:
        generator = _redis_stream()
    else:
        generator = event_stream()

    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/jobs/{job_id}/items", response_model=list[JobRead])
def get_playlist_items(
    job_id: str,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
) -> list[JobRead]:
    parent = _get_visible_job(session, job_id, user)
    if not parent.playlist_count or parent.playlist_count <= 1:
        raise HTTPException(status_code=404, detail="This job has no playlist items")
    items = session.exec(
        select(Job)
        .where(Job.parent_job_id == job_id)
        .order_by(Job.item_index)
    ).all()
    return [_to_read(item) for item in items]


@router.get("/jobs/{job_id}/file")
def download_file(
    job_id: str,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    job = _get_visible_job(session, job_id, user)
    if job.status == "expired":
        raise HTTPException(status_code=410, detail="File has expired")
    if job.status != "done" or not job.output_path:
        raise HTTPException(status_code=404, detail="File is not available")

    presigned = storage.presign(job)
    if presigned:
        return RedirectResponse(presigned)

    path = Path(job.output_path)
    if not path.exists():
        raise HTTPException(status_code=410, detail="File has expired")
    return FileResponse(
        path,
        media_type=job.mime or "application/octet-stream",
        filename=job.output_name or path.name,
    )


@router.get("/jobs/{job_id}/subtitles")
def download_subtitles(
    job_id: str,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    job = _get_visible_job(session, job_id, user)
    if job.status == "expired":
        raise HTTPException(status_code=410, detail="File has expired")
    if job.status != "done" or not job.subtitle_path:
        raise HTTPException(status_code=404, detail="Subtitles are not available")

    presigned = storage.presign_sidecar(job)
    if presigned:
        return RedirectResponse(presigned)

    path = Path(job.subtitle_path)
    if not path.exists():
        raise HTTPException(status_code=410, detail="Subtitles have expired")
    return FileResponse(
        path,
        media_type="text/vtt",
        filename=job.subtitle_name or path.name,
    )
