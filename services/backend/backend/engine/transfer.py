from __future__ import annotations

import logging
import urllib.error
from pathlib import Path
from typing import Callable

import yt_dlp

from ..config import settings
from ..security import UnsafeURLError, assert_public_http_url
from .remux import resolve_ffmpeg
from .types import Stream

logger = logging.getLogger(__name__)

try:  # MY-DL is our primary transfer engine
    from mydl.downloader import SegmentManager
    from mydl.http import ClientConfig

    HAS_MYDL = True
except Exception:  # pragma: no cover - degraded mode
    SegmentManager = None  # type: ignore[assignment]
    ClientConfig = None  # type: ignore[assignment]
    HAS_MYDL = False

ProgressCb = Callable[[int, "int | None", float, "float | None"], None]

SKIP_HEADERS = {"range", "accept-encoding"}
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def download_stream_mydl(
    stream: Stream,
    dest: Path,
    on_progress: ProgressCb,
    connections: int | None = None,
) -> Path:
    if not HAS_MYDL or SegmentManager is None or ClientConfig is None:
        raise RuntimeError("MY-DL engine is not installed")

    try:
        assert_public_http_url(stream.url)
    except UnsafeURLError as exc:
        raise RuntimeError(f"Refusing to download from a non-public address: {exc}") from exc

    headers = {
        key: value
        for key, value in stream.http_headers.items()
        if key.lower() not in SKIP_HEADERS
    }
    headers.setdefault("User-Agent", DEFAULT_UA)
    config = ClientConfig(headers=headers, timeout=30.0, retries=3)

    def hook(progress):  # mydl.Progress
        on_progress(progress.downloaded, progress.total, progress.speed_bps, progress.eta_seconds)

    manager = SegmentManager(config=config, progress=hook)
    dest.parent.mkdir(parents=True, exist_ok=True)
    logger.info("mydl transfer: %s (%s, protocol=%s)", dest.name, stream.format_id, stream.protocol)
    return Path(
        manager.download(
            stream.url,
            str(dest),
            connections=connections or settings.mydl_connections,
            resume=True,
        )
    )


def download_with_ytdlp(
    webpage_url: str,
    selector: str,
    out_base: Path,
    on_progress: ProgressCb,
    cookiefile: str | None = None,
) -> Path:
    def hook(status: dict) -> None:
        if status.get("status") != "downloading":
            return
        on_progress(
            int(status.get("downloaded_bytes") or 0),
            status.get("total_bytes") or status.get("total_bytes_estimate"),
            float(status.get("speed") or 0),
            status.get("eta"),
        )

    out_base.parent.mkdir(parents=True, exist_ok=True)
    for stale in out_base.parent.glob(out_base.name + ".*"):
        if stale.suffix.lower() not in {".part", ".ytdl"}:
            stale.unlink(missing_ok=True)

    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "format": selector,
        "outtmpl": str(out_base) + ".%(ext)s",
        "progress_hooks": [hook],
        "retries": 3,
        "fragment_retries": 5,
        "socket_timeout": 30,
        "concurrent_fragment_downloads": 4,
    }
    if cookiefile:
        opts["cookiefile"] = cookiefile
    ffmpeg = resolve_ffmpeg()
    if ffmpeg:
        opts["ffmpeg_location"] = ffmpeg

    logger.info("yt-dlp transfer: selector=%s url=%s", selector, webpage_url)
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([webpage_url])

    produced = [
        path
        for path in out_base.parent.glob(out_base.name + ".*")
        if path.suffix.lower() not in {".part", ".ytdl"}
    ]
    if not produced:
        raise RuntimeError("yt-dlp finished but produced no output file")
    return max(produced, key=lambda path: path.stat().st_size)


def is_expired_error(error: BaseException) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code in {401, 403, 410}
    message = str(error).lower()
    return any(token in message for token in ("403", "410", "expired", "forbidden"))
