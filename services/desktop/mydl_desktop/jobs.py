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

from backend.config import settings
from backend.cookies import apply_to_plan
from backend.engine.errors import classify_error
from backend.engine.extract import build_plan, extract_info
from backend.engine.playlist import (
    build_playlist_zip,
    entry_url,
    is_playlist,
    playlist_entries,
)
from backend.engine.remux import merge_streams, to_mp3
from backend.engine.router import direct_plan, is_direct_media_url
from backend.engine.storage import safe_download_name
from backend.engine.subs import chapters_from, chapters_text, fetch_subtitle
from backend.engine.types import PlaylistEmpty

from . import metering
from .activity import ActivityStore
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


class JobPaused(JobError):
    pass


@dataclass
class LocalJob:
    id: str
    url: str
    preset: str
    user_id: str
    user_email: str
    download_dir: str
    want_subtitles: bool = False
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
    subtitle_name: str | None = None
    subtitle_path: str | None = None
    subtitle_lang: str | None = None
    chapters: list[dict] | None = None
    playlist_index: int | None = None
    playlist_count: int | None = None
    error: str | None = None
    error_code: str | None = None
    retryable: bool = False
    created_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None

    def to_dict(self, key: str | None = None) -> dict[str, Any]:
        done = self.status == "done" and self.output_path
        subtitle_ready = done and self.subtitle_path and key
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
            "subtitle_name": self.subtitle_name,
            "subtitle_lang": self.subtitle_lang,
            "chapters": self.chapters,
            "playlist_index": self.playlist_index,
            "playlist_count": self.playlist_count,
            "error": self.error,
            "error_code": self.error_code,
            "retryable": self.retryable,
            "created_at": self.created_at.isoformat(),
            "download_url": (
                f"{AGENT_BASE}/v1/jobs/{self.id}/file?key={quote(key, safe='')}" if done and key else None
            ),
            "subtitle_url": (
                f"{AGENT_BASE}/v1/jobs/{self.id}/subtitles?key={quote(key, safe='')}"
                if subtitle_ready
                else None
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
        self._paused = threading.Event()
        self.activity = ActivityStore(config.state_path / "activity.json")

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    def paused(self) -> bool:
        return self._paused.is_set()

    def set_paused(self, paused: bool) -> None:
        if paused:
            self._paused.set()
        else:
            self._paused.clear()
        self.touch()

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for job in self._jobs.values() if job.status in ACTIVE_STATUSES)

    def active_jobs(self, limit: int = 5) -> list[dict[str, Any]]:
        with self._lock:
            jobs = [job for job in self._jobs.values() if job.status in ACTIVE_STATUSES]
            jobs.sort(key=lambda job: job.created_at)
            return [
                {
                    "id": job.id,
                    "title": job.title or job.url,
                    "stage": job.stage,
                    "progress": job.progress,
                    "speed_bps": job.speed_bps,
                    "eta_seconds": job.eta_seconds,
                    "downloaded_bytes": job.downloaded_bytes,
                    "total_bytes": job.total_bytes,
                }
                for job in jobs[:limit]
            ]

    def ensure_capacity(self) -> None:
        with self._lock:
            if self._paused.is_set():
                raise JobPaused(
                    "Downloads are paused in the Shorekeep tray menu. "
                    "Choose Resume downloads to continue."
                )
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
        subtitles: bool = False,
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
                want_subtitles=subtitles,
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
            plan, cookie_file, cookie_text, info = self._plan(job, work, cookie_browser)

            if plan is None and info is not None:
                self._run_playlist(job, info, work, cookie_file, cookie_text)
                return
            if plan is None:
                raise JobError("No downloadable streams were found")

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
            if job.want_subtitles and info is not None:
                chapters = chapters_from(info)
                if chapters:
                    self._set(job, chapters=chapters)

            final = self._download_with_refresh(plan, work, job, cookie_file, cookie_text)

            if plan.preset == "audio" and final.suffix.lower() != ".mp3":
                self._set(job, status="processing", stage="processing", progress=92)
                final = to_mp3(final, work / "audio.mp3")

            subtitle = fetch_subtitle(info, work) if job.want_subtitles and info is not None else None

            dest = self._save(final, plan.title, job)
            if subtitle is not None:
                path, lang = subtitle
                sub_name = safe_download_name(f"{plan.title}.{lang}", "vtt")
                sub_dest = _unique_path(self.config.download_path, sub_name)
                shutil.move(str(path), str(sub_dest))
                self._set(
                    job,
                    subtitle_name=sub_dest.name,
                    subtitle_path=str(sub_dest),
                    subtitle_lang=lang,
                )

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
            classified = classify_error(exc)
            logger.exception("desktop job %s failed", job.id)
            self._set(
                job,
                status="failed",
                stage="failed",
                error=classified.message,
                error_code=classified.code,
                retryable=classified.retryable,
                speed_bps=0.0,
                eta_seconds=None,
                finished_at=utcnow(),
            )
        finally:
            self._record(job)
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

    def _record(self, job: LocalJob) -> None:
        self.activity.record(
            {
                "id": job.id,
                "url": job.url,
                "title": job.title or job.output_name or job.url,
                "status": job.status if job.status in TERMINAL_STATUSES else "failed",
                "preset": job.preset,
                "output_name": job.output_name,
                "output_path": job.output_path,
                "output_size": job.output_size,
                "playlist_count": job.playlist_count,
                "error": job.error,
                "error_code": job.error_code,
                "saved_to": job.download_dir,
                "finished_at": (job.finished_at or utcnow()).isoformat(),
            }
        )

    def _plan(
        self, job: LocalJob, work: Path, cookie_browser: str
    ) -> tuple[Any, Path | None, str | None, dict | None]:
        mode = cookie_browser or self.config.cookie_browser
        if is_direct_media_url(job.url):
            return direct_plan(job.url, job.preset), None, None, None

        try:
            info = self._extract(job.url)
        except Exception as exc:
            if mode == "off" or not _needs_sign_in(exc):
                raise
            self._set(job, stage="extracting", progress=5)
            cookie_file = self._export_cookies(work, mode)
            cookie_text = cookie_file.read_text(encoding="utf-8", errors="replace")
            try:
                info = self._extract(job.url, str(cookie_file))
            except Exception as inner:
                if _needs_sign_in(inner) or "cookie" in str(inner).lower():
                    raise JobError(_cookie_failure_message(mode)) from inner
                raise
            return self._plan_from_info(job, info, cookie_file, cookie_text)

        return self._plan_from_info(job, info, None, None)

    def _extract(self, url: str, cookiefile: str | None = None) -> dict:
        return extract_info(
            url,
            cookiefile=cookiefile,
            playlist=True,
            limit=settings.playlist_max_items,
        )

    def _plan_from_info(
        self, job: LocalJob, info: dict, cookie_file: Path | None, cookie_text: str | None
    ) -> tuple[Any, Path | None, str | None, dict | None]:
        if is_playlist(info):
            entries = playlist_entries(info, settings.playlist_max_items)
            if len(entries) > 1:
                return None, cookie_file, cookie_text, info
            if not entries:
                raise PlaylistEmpty("This playlist has no downloadable videos.")
            info = self._extract(
                entry_url(entries[0]) or job.url,
                str(cookie_file) if cookie_file else None,
            )
        plan = build_plan(info, job.preset)
        if cookie_text:
            apply_to_plan(plan, cookie_text)
        return plan, cookie_file, cookie_text, info

    def _run_playlist(
        self,
        job: LocalJob,
        info: dict,
        work: Path,
        cookie_file: Path | None,
        cookie_text: str | None,
    ) -> None:
        title = info.get("title") or "Playlist"
        entries = playlist_entries(info, settings.playlist_max_items)
        self._set(
            job,
            title=title,
            thumbnail=info.get("thumbnail"),
            engine="ytdlp",
            status="downloading",
            stage="downloading",
            progress=5,
            playlist_count=len(entries),
            playlist_index=0,
        )
        result = build_playlist_zip(
            title=title,
            entries=entries,
            work=work,
            max_total_bytes=settings.playlist_max_total_bytes,
            download=lambda entry, item_dir, index, count: self._download_playlist_item(
                job, entry, item_dir, index, count, cookie_file, cookie_text
            ),
        )
        dest = self._save(result.path, title, job)
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
            playlist_index=result.saved,
            finished_at=utcnow(),
        )
        logger.info("desktop playlist job %s saved %s (%d bytes)", job.id, dest, size)

    def _download_playlist_item(
        self,
        job: LocalJob,
        entry: dict,
        item_dir: Path,
        index: int,
        count: int,
        cookie_file: Path | None,
        cookie_text: str | None,
    ) -> list[tuple[str, Path]]:
        url = entry_url(entry)
        if not url:
            raise JobError("This playlist item has no URL")
        start = 5 + (90 * index) / count
        end = 5 + (90 * (index + 1)) / count
        self._set(job, playlist_index=index + 1)

        info: dict | None = None
        cookiefile = str(cookie_file) if cookie_file else None
        if is_direct_media_url(url):
            plan = direct_plan(url, job.preset)
        else:
            info = extract_info(url, cookiefile=cookiefile)
            plan = build_plan(info, job.preset)
        if cookie_text:
            apply_to_plan(plan, cookie_text)

        final = self._download_with_refresh(plan, item_dir, job, cookie_file, cookie_text, start, end)
        if plan.preset == "audio" and final.suffix.lower() != ".mp3":
            final = to_mp3(final, item_dir / "audio.mp3")

        files = [(safe_download_name(plan.title, final.suffix.lstrip(".")), final)]
        if job.want_subtitles and info is not None:
            subtitle = fetch_subtitle(info, item_dir)
            if subtitle is not None:
                path, lang = subtitle
                files.append((safe_download_name(f"{plan.title}.{lang}", "vtt"), path))
            chapters = chapters_from(info)
            if chapters:
                item_title = str(entry.get("title") or f"Item {index + 1}")[:160]
                chapters_path = item_dir / safe_download_name(f"{item_title} chapters", "txt")
                chapters_path.write_text(chapters_text(chapters) + "\n", encoding="utf-8")
                files.append((safe_download_name(f"{plan.title} chapters", "txt"), chapters_path))
        return files

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
        start: float = 8,
        end: float = 90,
    ) -> Path:
        try:
            return self._download_once(plan, work, job, cookie_file, start, end)
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
            self._set(job, engine=refreshed.engine, status="downloading", stage="downloading", progress=start)
            return self._download_once(refreshed, work, job, cookie_file, start, end)

    def _download_once(
        self,
        plan: Any,
        work: Path,
        job: LocalJob,
        cookie_file: Path | None,
        start: float = 8,
        end: float = 90,
    ) -> Path:
        from backend.engine.transfer import download_stream_mydl, download_with_ytdlp

        cookiefile = str(cookie_file) if cookie_file else None

        if plan.engine == "ytdlp":
            return download_with_ytdlp(
                plan.webpage_url,
                plan.selector,
                work / "media",
                self._progress(job, start, end),
                cookiefile=cookiefile,
            )

        if plan.mode in {"progressive", "audio"}:
            stream = plan.video or plan.audio
            if stream is None:
                raise JobError("No downloadable stream was found")
            dest = work / f"media.{stream.ext or 'bin'}"
            return download_stream_mydl(stream, dest, self._progress(job, start, end - 2))

        if plan.video is None or plan.audio is None:
            raise JobError("No downloadable streams were found")
        mid = start + (end - start) * 0.55
        video_path = work / f"video.{plan.video.ext or 'bin'}"
        audio_path = work / f"audio.{plan.audio.ext or 'bin'}"
        video = download_stream_mydl(plan.video, video_path, self._progress(job, start, mid))
        audio = download_stream_mydl(plan.audio, audio_path, self._progress(job, mid, end - 3))
        self._set(job, status="processing", stage="processing", progress=end - 2)
        out = work / f"merged.{plan.ext}"
        return merge_streams(video, audio, out)

    def _save(self, final: Path, title: str, job: LocalJob) -> Path:
        directory = self.config.download_path
        directory.mkdir(parents=True, exist_ok=True)
        name = safe_download_name(title, final.suffix.lstrip(".") or "bin")
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
