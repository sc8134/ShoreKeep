from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlmodel import Session

from .config import settings
from .models import ACTIVE_STATUSES, DesktopJob, Job, User, utcnow

ALL_PRESETS = ("best", "1080p", "720p", "480p", "audio")
_PRESET_ORDER = {"480p": 1, "720p": 2, "1080p": 3, "best": 4}


@dataclass(frozen=True)
class PlanInfo:
    name: str
    jobs_per_hour: int
    max_active_jobs: int
    max_preset: str | None
    max_filesize_mb: int


@dataclass(frozen=True)
class Usage:
    used_this_hour: int
    limit_per_hour: int
    active_jobs: int
    max_active_jobs: int
    max_preset: str | None
    presets: tuple[str, ...]


def _demo_cap(plan: PlanInfo) -> PlanInfo:
    """Cap every plan for the public demo so it cannot be used as a free downloader."""
    if not settings.demo_mode:
        return plan
    max_preset = plan.max_preset
    if settings.demo_max_preset and (
        max_preset is None
        or _PRESET_ORDER.get(settings.demo_max_preset, 4) < _PRESET_ORDER.get(max_preset, 4)
    ):
        max_preset = settings.demo_max_preset
    return PlanInfo(
        name=plan.name,
        jobs_per_hour=min(plan.jobs_per_hour, settings.demo_jobs_per_hour),
        max_active_jobs=min(plan.max_active_jobs, settings.demo_max_active_jobs),
        max_preset=max_preset,
        max_filesize_mb=min(plan.max_filesize_mb, settings.demo_max_filesize_mb),
    )


def plan_for(user: User | None) -> PlanInfo:
    if user is None:
        return _demo_cap(
            PlanInfo(
                name="anonymous",
                jobs_per_hour=settings.anon_jobs_per_ip_hour,
                max_active_jobs=settings.anon_max_active_jobs,
                max_preset=settings.anon_max_preset or None,
                max_filesize_mb=settings.max_filesize_mb,
            )
        )
    if user.plan == "pro":
        return _demo_cap(
            PlanInfo(
                name="pro",
                jobs_per_hour=settings.pro_jobs_per_user_hour,
                max_active_jobs=settings.pro_max_active_jobs,
                max_preset=None,
                max_filesize_mb=max(settings.max_filesize_mb, settings.pro_max_filesize_mb),
            )
        )
    return _demo_cap(
        PlanInfo(
            name="free",
            jobs_per_hour=settings.free_jobs_per_user_hour,
            max_active_jobs=settings.free_max_active_jobs,
            max_preset=None,
            max_filesize_mb=settings.max_filesize_mb,
        )
    )


def preset_allowed(plan: PlanInfo, preset: str) -> bool:
    if preset == "audio" or plan.max_preset is None:
        return True
    return _PRESET_ORDER.get(preset, 99) <= _PRESET_ORDER.get(plan.max_preset, 4)


def allowed_presets(plan: PlanInfo) -> tuple[str, ...]:
    return tuple(preset for preset in ALL_PRESETS if preset_allowed(plan, preset))


def usage(session: Session, user: User | None, client_hash: str | None) -> Usage:
    """Quota usage shared by cloud jobs (job table) and desktop jobs (desktopjob table)."""
    plan = plan_for(user)
    now = utcnow()
    cutoff = now - timedelta(hours=1)

    hourly = select(func.count()).select_from(Job).where(Job.created_at >= cutoff)
    active = select(func.count()).select_from(Job).where(Job.status.in_(ACTIVE_STATUSES))
    if user is not None:
        hourly = hourly.where(Job.user_id == user.id)
        active = active.where(Job.user_id == user.id)
    else:
        hourly = hourly.where(Job.user_id.is_(None), Job.client_hash == client_hash)
        active = active.where(Job.user_id.is_(None), Job.client_hash == client_hash)

    desktop_hourly = 0
    desktop_active = 0
    if user is not None:
        desktop_hourly = int(
            session.scalar(
                select(func.count())
                .select_from(DesktopJob)
                .where(DesktopJob.user_id == user.id, DesktopJob.created_at >= cutoff)
            )
            or 0
        )
        desktop_active_q = select(func.count()).select_from(DesktopJob).where(
            DesktopJob.user_id == user.id, DesktopJob.status == "running"
        )
        if settings.job_timeout_seconds > 0:
            desktop_active_q = desktop_active_q.where(
                DesktopJob.created_at >= now - timedelta(seconds=settings.job_timeout_seconds)
            )
        desktop_active = int(session.scalar(desktop_active_q) or 0)

    return Usage(
        used_this_hour=int(session.scalar(hourly) or 0) + desktop_hourly,
        limit_per_hour=plan.jobs_per_hour,
        active_jobs=int(session.scalar(active) or 0) + desktop_active,
        max_active_jobs=plan.max_active_jobs,
        max_preset=plan.max_preset,
        presets=allowed_presets(plan),
    )
