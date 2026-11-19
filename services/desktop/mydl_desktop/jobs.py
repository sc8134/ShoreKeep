from __future__ import annotations

import logging
import mimetypes
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from backend.cookies import apply_to_plan
from backend.engine.extract import build_plan, extract_info
from backend.engine.remux import merge_streams, to_mp3
from backend.engine.router import direct_plan, is_direct_media_url

from . import metering
from .config import AGENT_HOST, AGENT_PORT, AgentConfig
from .cookies import cookie_candidates, export_cookies

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = {"queued", "extracting", "downloading", "processing"}
TERMINAL_STATUSES = {"done", "failed"}
AGENT_BASE = f"http://{AGENT_HOST}:{AGENT_PORT}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobError(RuntimeError):
    pass


class JobBusy(JobError):
    pass


@dataclass
class LocalJob:
    id: str
    url: str
    preset: str
    user_id: str
    user_email: str
    download_dir: str
    status: str = "queued"
    stage: str = "queued"
    progress: float = 0.0
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    speed_bps: float = 0.0
    eta_seconds: float | None = None
    title: str | None = None
    duration: float | None = None
    thumbnail: str | None = None
    engine: str | None = None
    output_name: str | None = None
    output_path: str | None = None
    output_size: int | None = None
    mime: str | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None

    def to_dict(self, key: str | None = None) -> dict[str, Any]:
        done = self.status == "done" and self.output_path
        return {
            "id": self.id,
            "url": self.url,
            "preset": self.preset,
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "speed_bps": self.speed_bps,
            "eta_seconds": self.eta_seconds,
            "title": self.title,
            "duration": self.duration,
            "thumbnail": self.thumbnail,
            "engine": self.engine,
            "output_name": self.output_name,
            "output_size": self.output_size,
            "mime": self.mime,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "download_url": (
                f"{AGENT_BASE}/v1/jobs/{self.id}/file?key={quote(key, safe='')}" if done and key else None
            ),
            "client": "desktop",
            "saved_to": self.download_dir,
        }


class JobManager:
    def __init__(self, config: AgentConfig, max_workers: int = 2) -> None:
        self.config = config
        self._jobs: dict[str, LocalJob] = {}
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="mydl-job")
        self.max_workers = max_workers
        self.last_activity = time.monotonic()

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for job in self._jobs.values() if job.status in ACTIVE_STATUSES)

    def ensure_capacity(self) -> None:
        with self._lock:
            if self.active_count() >= self.max_workers:
                raise JobBusy(
                    f"The desktop app is already processing {self.max_workers} downloads. "
                    "Wait for one to finish and retry."
                )

    def submit(
        self,
        *,
        url: str,
        preset: str,
        job_id: str,
        user: dict[str, str],
        cookie_browser: str,
    ) -> LocalJob:
        with self._lock:
            self.ensure_capacity()
            job = LocalJob(
                id=job_id,
                url=url,
                preset=preset,
                user_id=user.get("id", ""),
                user_email=user.get("email", ""),
                download_dir=str(self.config.download_path),
            )
            self._jobs[job_id] = job
            self._prune()
        self.touch()
        self._executor.submit(self._run, job, cookie_browser)
        return job

    def snapshot(self, job_id: str, key: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.to_dict(key) if job else None

    def get(self, job_id: str) -> LocalJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_for_user(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            jobs = [job for job in self._jobs.values() if job.user_id == user_id]
            jobs.sort(key=lambda job: job.created_at, reverse=True)
            return [job.to_dict() for job in jobs[:limit]]

    def _prune(self) -> None:
        if len(self._jobs) <= 40:
            return
        finished = sorted(
            (job for job in self._jobs.values() if job.status in TERMINAL_STATUSES),
            key=lambda job: job.created_at,
        )
        for job in finished[: len(self._jobs) - 40]:
            self._jobs.pop(job.id, None)

    def _set(self, job: LocalJob, **fields: Any) -> None:
        with self._lock:
            for key, value in fields.items():
                setattr(job, key, value)
        self.touch()

    def _progress(self, job: LocalJob, start: float, end: float):
        state = {"emit": 0.0, "percent": start}

        def callback(downloaded: int, total: int | None, speed: float, eta: float | None) -> None:
            now = time.monotonic()
            if now - state["emit"] < 0.4:
                return
            state["emit"] = now
            percent = start
            if total and total > 0:
                percent = start + (end - start) * min(downloaded / total, 1.0)
            percent = max(state["percent"], min(percent, end))
            state["percent"] = percent
            self._set(
                job,
                progress=round(percent, 1),
                downloaded_bytes=int(downloaded or 0),
                total_bytes=int(total) if total else None,
                speed_bps=float(speed or 0),
                eta_seconds=float(eta) if eta is not None else None,
            )

        return callback

    def _run(self, job: LocalJob, cookie_browser: str) -> None:
        work = Path(tempfile.mkdtemp(prefix=f"shorekeep-{job.id[:8]}-"))
        cookie_file: Path | None = None
        cookie_text: str | None = None
        try:
            self._set(job, status="extracting", stage="extracting", progress=3)
            plan, cookie_file, cookie_text = self._plan(job, work, cookie_browser)
            self._set(
                job,
                title=plan.title,
                duration=plan.duration,
                thumbnail=plan.thumbnail,
                engine=plan.engine,
                status="downloading",
                stage="downloading",
                progress=8,
            )

            final = self._download_with_refresh(plan, work, job, cookie_file, cookie_text)

            if plan.preset == "audio" and final.suffix.lower() != ".mp3":
                self._set(job, status="processing", stage="processing", progress=92)
                final = to_mp3(final, work / "audio.mp3")

            dest = self._save(final, plan, job)
            size = dest.stat().st_size
            self._set(
                job,
                status="done",
                stage="done",
                progress=100.0,
                downloaded_bytes=size,
                total_bytes=size,
                speed_bps=0.0,
                eta_seconds=0.0,
                output_name=dest.name,
                output_path=str(dest),
                output_size=size,
                mime=mimetypes.guess_type(dest.name)[0] or "application/octet-stream",
                finished_at=utcnow(),
            )
            logger.info("desktop job %s saved %s (%d bytes)", job.id, dest, size)
        except Exception as exc:
            logger.exception("desktop job %s failed", job.id)
            self._set(
                job,
                status="failed",
                stage="failed",
                error=_friendly_error(exc),
                speed_bps=0.0,
                eta_seconds=None,
                finished_at=utcnow(),
            )
        finally:
            metering.complete(
                self.config,
                job_id=job.id,
                status="done" if job.status == "done" else "failed",
                preset=job.preset,
                url=job.url,
                size_bytes=job.output_size or 0,
                error=job.error,
                started_at=job.created_at,
            )
            shutil.rmtree(work, ignore_errors=True)
            self.touch()

    def _plan(
        self, job: LocalJob, work: Path, cookie_browser: str
    ) -> tuple[Any, Path | None, str | None]:
        mode = cookie_browser or self.config.cookie_browser
        if is_direct_media_url(job.url):
            return direct_plan(job.url, job.preset), None, None

        try:
            info = extract_info(job.url)
            return build_plan(info, job.preset), None, None
        except Exception as exc:
            if mode == "off" or not _needs_sign_in(exc):
                raise

        self._set(job, stage="extracting", progress=5)
        cookie_file = self._export_cookies(work, mode)
        cookie_text = cookie_file.read_text(encoding="utf-8", errors="replace")
        try:
            info = extract_info(job.url, cookiefile=str(cookie_file))
        except Exception as exc:
            if _needs_sign_in(exc) or "cookie" in str(exc).lower():
                raise JobError(_cookie_failure_message(mode)) from exc
            raise
        plan = build_plan(info, job.preset)
        apply_to_plan(plan, cookie_text)
        return plan, cookie_file, cookie_text

    def _export_cookies(self, work: Path, mode: str) -> Path:
        failures: list[str] = []
        for browser in cookie_candidates(mode):
            try:
                return export_cookies(browser, work / "cookies.txt")
            except Exception as exc:
                logger.info("cookie export from %s failed: %s", browser, exc)
                failures.append(f"{browser}: {exc}")
        detail = "; ".join(failures) if failures else "no supported browser profile was found"
        raise JobError(
            "This site needs a signed-in session, but the app could not read cookies "
            f"automatically ({detail}). Sign in with Firefox and retry, or use the web "
            "app with a cookies.txt export."
        )

    def _download_with_refresh(
        self,
        plan: Any,
        work: Path,
        job: LocalJob,
        cookie_file: Path | None,
        cookie_text: str | None,
    ) -> Path:
        try:
            return self._download_once(plan, work, job, cookie_file)
        except Exception as exc:
            from backend.engine.transfer import is_expired_error

            if plan.direct or not plan.webpage_url or not is_expired_error(exc):
                raise
            logger.info("signed media URL expired; re-extracting %s", plan.webpage_url)
            refreshed = build_plan(
                extract_info(plan.webpage_url, cookiefile=str(cookie_file) if cookie_file else None),
                plan.preset,
            )
            if cookie_text:
                apply_to_plan(refreshed, cookie_text)
            self._set(job, engine=refreshed.engine, status="downloading", stage="downloading", progress=8)
            return self._download_once(refreshed, work, job, cookie_file)

    def _download_once(
        self, plan: Any, work: Path, job: LocalJob, cookie_file: Path | None
    ) -> Path:
        from backend.engine.transfer import download_stream_mydl, download_with_ytdlp

        cookiefile = str(cookie_file) if cookie_file else None

        if plan.engine == "ytdlp":
            return download_with_ytdlp(
                plan.webpage_url,
                plan.selector,
                work / "media",
                self._progress(job, 8, 90),
                cookiefile=cookiefile,
            )

        if plan.mode in {"progressive", "audio"}:
            stream = plan.video or plan.audio
            if stream is None:
                raise JobError("No downloadable stream was found")
            dest = work / f"media.{stream.ext or 'bin'}"
            return download_stream_mydl(stream, dest, self._progress(job, 8, 90))

        if plan.video is None or plan.audio is None:
            raise JobError("No downloadable streams were found")
        video_path = work / f"video.{plan.video.ext or 'bin'}"
        audio_path = work / f"audio.{plan.audio.ext or 'bin'}"
        video = download_stream_mydl(plan.video, video_path, self._progress(job, 8, 60))
        audio = download_stream_mydl(plan.audio, audio_path, self._progress(job, 60, 88))
        self._set(job, status="processing", stage="processing", progress=90)
        out = work / f"merged.{plan.ext}"
        return merge_streams(video, audio, out)

    def _save(self, final: Path, plan: Any, job: LocalJob) -> Path:
        from backend.engine.storage import safe_download_name

        directory = self.config.download_path
        directory.mkdir(parents=True, exist_ok=True)
        name = safe_download_name(plan.title, final.suffix.lstrip(".") or "bin")
        dest = _unique_path(directory, name)
        shutil.move(str(final), str(dest))
        return dest


def _unique_path(directory: Path, name: str) -> Path:
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for index in range(1, 1000):
        candidate = directory / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
    return directory / f"{stem} ({uuid4().hex[:6]}){suffix}"


def _needs_sign_in(error: BaseException) -> bool:
    message = str(error).lower()
    tokens = (
        "sign in",
        "log in",
        "login",
        "not a bot",
        "confirm you",
        "cookies",
        "account",
        "members",
        "authenticat",
        "private video",
        "age-restricted",
        "age restricted",
    )
    return any(token in message for token in tokens)


def _cookie_failure_message(mode: str) -> str:
    return (
        "This site needs a signed-in session, and the cookies the app read were not accepted. "
        "Sign in to the site in Firefox (automatic cookie reading is most reliable there) "
        "and retry. If you only use Chrome or Edge, browsers released after mid-2024 encrypt "
        "cookies in a way that blocks automatic export. Use the web app's cookies.txt option instead."
    )


def _friendly_error(error: BaseException) -> str:
    message = str(error).strip() or error.__class__.__name__
    lowered = message.lower()
    if isinstance(error, JobBusy):
        return message
    if _needs_sign_in(error) and "cookies" not in lowered and "download" not in lowered:
        return (
            "The site is asking for a signed-in session. Sign in to it in Firefox and retry; "
            "the desktop app will pick up your cookies automatically."
        )
    if "drm" in lowered:
        return "DRM-protected content is not supported."
    if "private video" in lowered:
        return "This video is private."
    if "unsupported url" in lowered:
        return "This URL is not supported."
    return message[-700:]
