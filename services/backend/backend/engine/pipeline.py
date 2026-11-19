from __future__ import annotations

import logging
import shutil
import threading
import time
from pathlib import Path

from sqlmodel import Session

from ..config import settings
from ..cookies import CookieError, apply_to_plan, decrypt_cookies, write_cookie_file
from ..db import engine
from ..models import Job, utcnow
from ..security import UnsafeURLError, assert_public_http_url
from .extract import build_plan, extract_info
from .remux import merge_streams, to_mp3
from .router import direct_plan, is_direct_media_url
from .storage import storage
from .transfer import download_stream_mydl, download_with_ytdlp, is_expired_error
from .types import JobTimeout, MediaPlan, SizeLimitExceeded

logger = logging.getLogger(__name__)


class Reporter:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self._last_write = 0.0
        self._last_percent = 0.0
        self._started = time.monotonic()
        self._lock = threading.RLock()

    def _write(self, force: bool = False, **fields) -> None:
        with self._lock:
            now = time.monotonic()
            if not force and now - self._last_write < 0.75:
                return
            try:
                with Session(engine) as session:
                    job = session.get(Job, self.job_id)
                    if job is None:
                        return
                    for key, value in fields.items():
                        setattr(job, key, value)
                    session.add(job)
                    session.commit()
            except Exception:
                logger.exception("failed to persist state for job %s", self.job_id)
                return
            self._last_write = now

    def stage(self, stage: str, status: str, progress: float | None = None, **extra) -> None:
        fields: dict = {"stage": stage, "status": status, **extra}
        if progress is not None:
            fields["progress"] = round(progress, 1)
            self._last_percent = max(self._last_percent, progress)
        self._write(force=True, **fields)

    def set_meta(self, plan: MediaPlan) -> None:
        self._write(
            force=True,
            title=plan.title,
            duration=plan.duration,
            thumbnail=plan.thumbnail,
            engine=plan.engine,
        )

    def download_progress(self, start: float, end: float):
        def callback(downloaded: int, total: int | None, speed: float, eta: float | None) -> None:
            self._enforce_limits(downloaded, total)
            percent = start
            if total and total > 0:
                percent = start + (end - start) * min(downloaded / total, 1.0)
            percent = max(self._last_percent, min(percent, end))
            self._last_percent = percent
            self._write(
                progress=round(percent, 1),
                downloaded_bytes=int(downloaded or 0),
                total_bytes=int(total) if total else None,
                speed_bps=float(speed or 0),
                eta_seconds=float(eta) if eta is not None else None,
            )

        return callback

    def _enforce_limits(self, downloaded: int, total: int | None) -> None:
        limit = settings.max_filesize_bytes
        if limit > 0:
            if total and total > limit:
                raise SizeLimitExceeded(f"File is larger than the {settings.max_filesize_mb} MB limit")
            if downloaded > limit:
                raise SizeLimitExceeded(f"Download exceeded the {settings.max_filesize_mb} MB limit")
        if settings.job_timeout_seconds > 0 and time.monotonic() - self._started > settings.job_timeout_seconds:
            raise JobTimeout(f"Job exceeded the {settings.job_timeout_seconds}s time limit")

    def complete(self, stored) -> None:
        self._write(
            force=True,
            status="done",
            stage="done",
            progress=100.0,
            downloaded_bytes=stored.size,
            total_bytes=stored.size,
            speed_bps=0.0,
            eta_seconds=0.0,
            output_name=stored.name,
            output_path=stored.path,
            output_size=stored.size,
            mime=stored.mime,
            finished_at=utcnow(),
        )

    def fail(self, message: str) -> None:
        self._write(
            force=True,
            status="failed",
            stage="failed",
            error=message,
            speed_bps=0.0,
            eta_seconds=None,
            finished_at=utcnow(),
        )


def run_job(job_id: str) -> None:
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        url, preset, cookies_enc = job.url, job.preset, job.cookies_enc

    reporter = Reporter(job_id)
    work = settings.work_dir / job_id
    cookie_text: str | None = None
    cookie_file: str | None = None
    try:
        assert_public_http_url(url)
        work.mkdir(parents=True, exist_ok=True)

        if cookies_enc:
            cookie_text = decrypt_cookies(cookies_enc)
            cookie_file = str(write_cookie_file(cookie_text, work / "cookies.txt"))

        reporter.stage("extracting", "extracting", 3)
        if is_direct_media_url(url):
            plan = direct_plan(url, preset)
        else:
            plan = build_plan(
                extract_info(url, cookiefile=cookie_file), preset, settings.max_filesize_bytes
            )
        if cookie_text:
            apply_to_plan(plan, cookie_text)
        reporter.set_meta(plan)

        reporter.stage("downloading", "downloading", 8)
        final = _download_with_refresh(plan, work, reporter, cookie_file, cookie_text)

        if plan.preset == "audio" and final.suffix.lower() != ".mp3":
            reporter.stage("processing", "processing", 90)
            final = to_mp3(final, work / "audio.mp3")

        reporter.stage("uploading", "uploading", 95)
        stored = storage.save(final, job_id, plan.title)
        reporter.complete(stored)
    except UnsafeURLError as exc:
        _safe_fail(reporter, str(exc))
    except CookieError as exc:
        _safe_fail(reporter, friendly_error(exc))
    except Exception as exc:
        logger.exception("job %s failed", job_id)
        _safe_fail(reporter, friendly_error(exc))
    finally:
        if cookies_enc:
            _clear_cookies(job_id)
        shutil.rmtree(work, ignore_errors=True)


def _clear_cookies(job_id: str) -> None:
    try:
        with Session(engine) as session:
            job = session.get(Job, job_id)
            if job is not None and job.cookies_enc:
                job.cookies_enc = None
                session.add(job)
                session.commit()
    except Exception:
        logger.exception("could not clear cookies for job %s", job_id)


def _safe_fail(reporter: Reporter, message: str) -> None:
    try:
        reporter.fail(message)
    except Exception:
        logger.exception("failed to mark job %s as failed", reporter.job_id)


def _download_with_refresh(
    plan: MediaPlan,
    work: Path,
    reporter: Reporter,
    cookiefile: str | None = None,
    cookie_text: str | None = None,
) -> Path:
    try:
        return _download_once(plan, work, reporter, cookiefile)
    except (SizeLimitExceeded, JobTimeout):
        raise
    except Exception as exc:
        if plan.direct or not plan.webpage_url or not is_expired_error(exc):
            raise
        logger.info("signed media URL expired; re-extracting %s", plan.webpage_url)
        refreshed = build_plan(
            extract_info(plan.webpage_url, cookiefile=cookiefile),
            plan.preset,
            settings.max_filesize_bytes,
        )
        if cookie_text:
            apply_to_plan(refreshed, cookie_text)
        reporter.set_meta(refreshed)
        reporter.stage("downloading", "downloading", 8)
        return _download_once(refreshed, work, reporter, cookiefile)


def _download_once(
    plan: MediaPlan,
    work: Path,
    reporter: Reporter,
    cookiefile: str | None = None,
) -> Path:
    if plan.engine == "ytdlp":
        return download_with_ytdlp(
            plan.webpage_url,
            plan.selector,
            work / "media",
            reporter.download_progress(8, 90),
            cookiefile=cookiefile,
        )

    if plan.mode in {"progressive", "audio"}:
        stream = plan.video or plan.audio
        assert stream is not None
        dest = work / f"media.{stream.ext or 'bin'}"
        return download_stream_mydl(stream, dest, reporter.download_progress(8, 88))

    assert plan.video is not None and plan.audio is not None
    video_path = work / f"video.{plan.video.ext or 'bin'}"
    audio_path = work / f"audio.{plan.audio.ext or 'bin'}"
    video = download_stream_mydl(plan.video, video_path, reporter.download_progress(8, 60))
    audio = download_stream_mydl(plan.audio, audio_path, reporter.download_progress(60, 85))

    reporter.stage("processing", "processing", 88)
    out = work / f"merged.{plan.ext}"
    return merge_streams(video, audio, out)


def friendly_error(error: BaseException) -> str:
    if isinstance(error, JobTimeout):
        return str(error)
    if isinstance(error, SizeLimitExceeded):
        return str(error)
    message = str(error).strip() or error.__class__.__name__
    lowered = message.lower()
    if "sign in to confirm" in lowered or "not a bot" in lowered:
        return "The source is blocking automated access right now (bot check). Please retry later."
    if "drm" in lowered:
        return "DRM-protected content is not supported."
    if "private video" in lowered:
        return "This video is private."
    if "unsupported url" in lowered:
        return "This URL is not supported."
    if "cookies could not be decrypted" in lowered:
        return "Stored cookies could not be read. Re-submit the job with fresh cookies."
    if "cookie" in lowered and any(
        token in lowered
        for token in ("expired", "invalid", "netscape", "rejected", "failed to load", "malformed")
    ):
        return "Your cookies were rejected or expired. Export a fresh cookies.txt and try again."
    if any(token in lowered for token in ("login required", "log in", "sign in required", "authenticate")):
        return "This site requires a login. Sign in here, add your own cookies.txt, and retry."
    return message[-700:]
