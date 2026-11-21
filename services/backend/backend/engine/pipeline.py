from __future__ import annotations

import json
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
from .errors import classify_error
from .extract import build_plan, extract_info
from .playlist import build_playlist_zip, entry_title, entry_url, is_playlist, playlist_entries
from .remux import merge_streams, to_mp3
from .router import direct_plan, is_direct_media_url
from .storage import safe_download_name, storage
from .subs import chapters_from, chapters_text, fetch_subtitle
from .transfer import download_stream_mydl, download_with_ytdlp, is_expired_error
from .types import JobTimeout, MediaPlan, PlaylistEmpty, SizeLimitExceeded

logger = logging.getLogger(__name__)


class Reporter:
    def __init__(self, job_id: str, max_bytes: int | None = None) -> None:
        self.job_id = job_id
        self.max_bytes = max_bytes if max_bytes and max_bytes > 0 else settings.max_filesize_bytes
        self.max_filesize_mb = round(self.max_bytes / (1024 * 1024)) if self.max_bytes > 0 else 0
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

    def set_chapters(self, chapters: list[dict]) -> None:
        self._write(force=True, chapters=json.dumps(chapters))

    def set_subtitle(self, stored, lang: str) -> None:
        self._write(
            force=True,
            subtitle_name=stored.name,
            subtitle_path=stored.path,
            subtitle_lang=lang,
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
        limit = self.max_bytes
        if limit > 0:
            if total and total > limit:
                raise SizeLimitExceeded(f"File is larger than the {self.max_filesize_mb} MB limit")
            if downloaded > limit:
                raise SizeLimitExceeded(f"Download exceeded the {self.max_filesize_mb} MB limit")
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

    def fail(self, message: str, code: str = "unknown") -> None:
        self._write(
            force=True,
            status="failed",
            stage="failed",
            error=message,
            error_code=code,
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
        want_subtitles = bool(job.want_subtitles)
        limit_mb = job.max_filesize_mb or settings.max_filesize_mb

    reporter = Reporter(job_id, limit_mb * 1024 * 1024)
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
            info = None
        else:
            info = extract_info(
                url, cookiefile=cookie_file, playlist=True, limit=settings.playlist_max_items
            )
            if is_playlist(info):
                entries = playlist_entries(info, settings.playlist_max_items)
                if len(entries) > 1:
                    _run_playlist(
                        job_id,
                        info,
                        entries,
                        preset,
                        want_subtitles,
                        work,
                        reporter,
                        cookie_file,
                        cookie_text,
                    )
                    return
                if not entries:
                    raise PlaylistEmpty("This playlist has no downloadable videos.")
                info = extract_info(entry_url(entries[0]) or url, cookiefile=cookie_file)
            plan = build_plan(info, preset, reporter.max_bytes)

        if cookie_text:
            apply_to_plan(plan, cookie_text)
        reporter.set_meta(plan)

        if want_subtitles and info is not None:
            chapters = chapters_from(info)
            if chapters:
                reporter.set_chapters(chapters)

        reporter.stage("downloading", "downloading", 8)
        final = _download_with_refresh(plan, work, reporter, cookie_file, cookie_text)

        if plan.preset == "audio" and final.suffix.lower() != ".mp3":
            reporter.stage("processing", "processing", 90)
            final = to_mp3(final, work / "audio.mp3")

        subtitle = fetch_subtitle(info, work) if want_subtitles and info is not None else None

        reporter.stage("uploading", "uploading", 95)
        stored = storage.save(final, job_id, plan.title)
        if subtitle is not None:
            path, lang = subtitle
            try:
                sidecar = storage.save_sidecar(path, job_id, plan.title, lang)
                reporter.set_subtitle(sidecar, lang)
            except Exception:
                logger.exception("could not store subtitles for job %s", job_id)
        reporter.complete(stored)
    except UnsafeURLError as exc:
        _safe_fail(reporter, exc)
    except CookieError as exc:
        _safe_fail(reporter, exc)
    except Exception as exc:
        logger.exception("job %s failed", job_id)
        _safe_fail(reporter, exc)
    finally:
        if cookies_enc:
            _clear_cookies(job_id)
        shutil.rmtree(work, ignore_errors=True)


def _run_playlist(
    job_id: str,
    info: dict,
    entries: list[dict],
    preset: str,
    want_subtitles: bool,
    work: Path,
    reporter: Reporter,
    cookie_file: str | None,
    cookie_text: str | None,
) -> None:
    title = info.get("title") or "Playlist"
    reporter.stage(
        "downloading",
        "downloading",
        5,
        title=title,
        thumbnail=info.get("thumbnail"),
        playlist_count=len(entries),
        playlist_index=0,
    )

    def download(entry: dict, item_dir: Path, index: int, count: int) -> list[tuple[str, Path]]:
        return _download_playlist_item(
            entry,
            item_dir,
            index,
            count,
            preset,
            want_subtitles,
            reporter,
            cookie_file,
            cookie_text,
        )

    result = build_playlist_zip(
        title=title,
        entries=entries,
        work=work,
        max_total_bytes=settings.playlist_max_total_bytes,
        download=download,
    )
    reporter.stage("uploading", "uploading", 95, playlist_index=result.saved)
    stored = storage.save(result.path, job_id, title)
    reporter.complete(stored)


def _download_playlist_item(
    entry: dict,
    item_dir: Path,
    index: int,
    count: int,
    preset: str,
    want_subtitles: bool,
    reporter: Reporter,
    cookie_file: str | None,
    cookie_text: str | None,
) -> list[tuple[str, Path]]:
    url = entry_url(entry)
    if not url:
        raise RuntimeError("This playlist item has no URL")
    item_title = entry_title(entry, index)
    start = 5 + (90 * index) / count
    end = 5 + (90 * (index + 1)) / count
    reporter.stage("downloading", "downloading", None, playlist_index=index + 1)

    info: dict | None = None
    if is_direct_media_url(url):
        plan = direct_plan(url, preset)
    else:
        info = extract_info(url, cookiefile=cookie_file)
        plan = build_plan(info, preset, reporter.max_bytes)
    if cookie_text:
        apply_to_plan(plan, cookie_text)

    final = _download_with_refresh(plan, item_dir, reporter, cookie_file, cookie_text, start, end)
    if plan.preset == "audio" and final.suffix.lower() != ".mp3":
        final = to_mp3(final, item_dir / "audio.mp3")

    files = [(safe_download_name(plan.title, final.suffix.lstrip(".")), final)]
    if want_subtitles and info is not None:
        subtitle = fetch_subtitle(info, item_dir)
        if subtitle is not None:
            path, lang = subtitle
            files.append((safe_download_name(f"{plan.title}.{lang}", "vtt"), path))
        chapters = chapters_from(info)
        if chapters:
            chapters_path = item_dir / safe_download_name(f"{item_title} chapters", "txt")
            chapters_path.write_text(chapters_text(chapters) + "\n", encoding="utf-8")
            files.append((safe_download_name(f"{plan.title} chapters", "txt"), chapters_path))
    return files


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


def _safe_fail(reporter: Reporter, error: BaseException) -> None:
    try:
        info = classify_error(error)
        reporter.fail(info.message, code=info.code)
    except Exception:
        logger.exception("failed to mark job %s as failed", reporter.job_id)


def _download_with_refresh(
    plan: MediaPlan,
    work: Path,
    reporter: Reporter,
    cookiefile: str | None = None,
    cookie_text: str | None = None,
    start: float = 8,
    end: float = 90,
) -> Path:
    try:
        return _download_once(plan, work, reporter, cookiefile, start, end)
    except (SizeLimitExceeded, JobTimeout):
        raise
    except Exception as exc:
        if plan.direct or not plan.webpage_url or not is_expired_error(exc):
            raise
        logger.info("signed media URL expired; re-extracting %s", plan.webpage_url)
        refreshed = build_plan(
            extract_info(plan.webpage_url, cookiefile=cookiefile),
            plan.preset,
            reporter.max_bytes,
        )
        if cookie_text:
            apply_to_plan(refreshed, cookie_text)
        reporter.set_meta(refreshed)
        reporter.stage("downloading", "downloading", start)
        return _download_once(refreshed, work, reporter, cookiefile, start, end)


def _download_once(
    plan: MediaPlan,
    work: Path,
    reporter: Reporter,
    cookiefile: str | None = None,
    start: float = 8,
    end: float = 90,
) -> Path:
    if plan.engine == "ytdlp":
        return download_with_ytdlp(
            plan.webpage_url,
            plan.selector,
            work / "media",
            reporter.download_progress(start, end),
            cookiefile=cookiefile,
        )

    if plan.mode in {"progressive", "audio"}:
        stream = plan.video or plan.audio
        assert stream is not None
        dest = work / f"media.{stream.ext or 'bin'}"
        return download_stream_mydl(stream, dest, reporter.download_progress(start, end - 2))

    assert plan.video is not None and plan.audio is not None
    mid = start + (end - start) * 0.55
    video_path = work / f"video.{plan.video.ext or 'bin'}"
    audio_path = work / f"audio.{plan.audio.ext or 'bin'}"
    video = download_stream_mydl(plan.video, video_path, reporter.download_progress(start, mid))
    audio = download_stream_mydl(plan.audio, audio_path, reporter.download_progress(mid, end - 3))

    reporter.stage("processing", "processing", end - 2)
    out = work / f"merged.{plan.ext}"
    return merge_streams(video, audio, out)


def friendly_error(error: BaseException) -> str:
    return classify_error(error).message
