from __future__ import annotations

import logging
import shutil
import time
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import update
from sqlmodel import Session, delete, select

from ..agent_auth import purge_expired_pairs
from ..auth import purge_expired_sessions
from ..config import settings
from ..db import IS_POSTGRES, engine, init_db
from ..models import RUNNING_STATUSES, DesktopJob, Job, utcnow
from .pipeline import run_job
from .storage import storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

WORKER_ID = f"worker-{uuid4().hex[:8]}"


def claim_job() -> str | None:
    with Session(engine) as session:
        now = utcnow()
        statement = (
            select(Job)
            .where(Job.status == "queued")
            .where((Job.run_after == None) | (Job.run_after <= now))  # noqa: E711
            .order_by(Job.priority.desc(), Job.created_at)
            .limit(1)
        )
        if IS_POSTGRES:
            statement = statement.with_for_update(skip_locked=True)
        candidate = session.exec(statement).first()
        if candidate is None:
            return None
        result = session.execute(
            update(Job)
            .where(Job.id == candidate.id, Job.status == "queued")
            .values(
                status="extracting",
                stage="extracting",
                worker_id=WORKER_ID,
                started_at=utcnow(),
            )
        )
        session.commit()
        return candidate.id if result.rowcount else None


def _auto_retry(job_id: str) -> None:
    """Re-queue a failed retryable job if max_retries allows it."""
    from .errors import retryable_code
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if job is None or job.status != "failed":
            return
        max_r = int(job.max_retries or 0)
        retried = int(job.retry_count or 0)
        if max_r <= 0 or retried >= max_r:
            return
        if not retryable_code(job.error_code):
            return
        # Exponential backoff: 2^retry × 15s, capped at 10 min.
        backoff = min(15 * (2 ** retried), 600)
        job.status = "queued"
        job.stage = "queued"
        job.error = None
        job.error_code = None
        job.retry_count = retried + 1
        job.run_after = utcnow() + timedelta(seconds=backoff)
        job.progress = 0.0
        job.downloaded_bytes = 0
        job.speed_bps = 0.0
        job.eta_seconds = None
        job.started_at = None
        job.finished_at = None
        session.add(job)
        session.commit()
        logger.info("auto-retry %d/%d for job %s (backoff %ds)", retried + 1, max_r, job_id, backoff)


def sweep_expired() -> None:
    cutoff = utcnow() - timedelta(seconds=settings.retention_seconds)
    with Session(engine) as session:
        jobs = session.exec(
            select(Job).where(Job.status == "done", Job.finished_at < cutoff)
        ).all()
        for job in jobs:
            try:
                storage.delete(job)
            except Exception:
                logger.exception("failed to delete file for job %s", job.id)
            job.status = "expired"
            job.stage = "expired"
            job.output_path = None
            job.subtitle_path = None
            job.cookies_enc = None
            session.add(job)
        if jobs:
            session.commit()
            logger.info("expired %d job(s)", len(jobs))


def reap_stale_jobs() -> None:
    if settings.job_timeout_seconds <= 0:
        return
    cutoff = utcnow() - timedelta(seconds=settings.job_timeout_seconds)
    stale_ids: list[str] = []
    with Session(engine) as session:
        jobs = session.exec(
            select(Job).where(Job.status.in_(RUNNING_STATUSES), Job.started_at < cutoff)
        ).all()
        for job in jobs:
            job.status = "failed"
            job.stage = "failed"
            job.error = "Job timed out or the worker stopped. Please retry."
            job.error_code = "timeout"
            job.finished_at = utcnow()
            job.speed_bps = 0.0
            job.eta_seconds = None
            job.cookies_enc = None
            session.add(job)
            stale_ids.append(job.id)
        if stale_ids:
            session.commit()
    for job_id in stale_ids:
        shutil.rmtree(settings.work_dir / job_id, ignore_errors=True)
    if stale_ids:
        logger.info("failed %d stale job(s)", len(stale_ids))


def reap_stale_desktop_jobs() -> None:
    """Desktop reservations whose agent stopped reporting stop counting as active."""
    if settings.job_timeout_seconds <= 0:
        return
    cutoff = utcnow() - timedelta(seconds=settings.job_timeout_seconds)
    with Session(engine) as session:
        jobs = session.exec(
            select(DesktopJob).where(DesktopJob.status == "running", DesktopJob.created_at < cutoff)
        ).all()
        for job in jobs:
            job.status = "failed"
            job.error = "The desktop app did not report a result in time."
            job.finished_at = utcnow()
            session.add(job)
        if jobs:
            session.commit()
            logger.info("closed %d stale desktop job(s)", len(jobs))


def prune_desktop_usage() -> None:
    if settings.desktop_usage_retention_days <= 0:
        return
    cutoff = utcnow() - timedelta(days=settings.desktop_usage_retention_days)
    with Session(engine) as session:
        result = session.exec(
            delete(DesktopJob).where(
                DesktopJob.created_at < cutoff, DesktopJob.status != "running"
            )
        )
        session.commit()
        if result.rowcount:
            logger.info("pruned %d old desktop usage record(s)", result.rowcount)


def sweep_sessions() -> None:
    with Session(engine) as session:
        removed = purge_expired_sessions(session)
        pairs = purge_expired_pairs(session)
    if removed or pairs:
        logger.info("purged %d session(s) and %d pairing token(s)", removed, pairs)


def main() -> None:
    init_db()
    # Reload persisted bandwidth limit into memory.
    from ..api.bandwidth import _load_persisted
    _load_persisted()
    logger.info("worker %s started (poll=%.1fs)", WORKER_ID, settings.worker_poll_seconds)
    last_sweep = 0.0
    while True:
        now = time.monotonic()
        if now - last_sweep > 60:
            for task in (
                sweep_expired,
                reap_stale_jobs,
                reap_stale_desktop_jobs,
                prune_desktop_usage,
                sweep_sessions,
            ):
                try:
                    task()
                except Exception:
                    logger.exception("%s failed", task.__name__)
            last_sweep = now

        try:
            job_id = claim_job()
        except Exception:
            logger.exception("could not claim a job")
            time.sleep(settings.worker_poll_seconds)
            continue

        if job_id is None:
            time.sleep(settings.worker_poll_seconds)
            continue

        logger.info("claimed job %s", job_id)
        started = time.monotonic()
        try:
            run_job(job_id)
        except Exception:
            logger.exception("unhandled failure in job %s", job_id)
        logger.info("job %s finished in %.1fs", job_id, time.monotonic() - started)
        _auto_retry(job_id)


if __name__ == "__main__":
    main()
