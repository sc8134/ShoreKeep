from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse

from .types import MediaPlan, Stream

DIRECT_MEDIA_EXTS = {
    ".mp4", ".m4v", ".mov", ".webm", ".mkv",
    ".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus",
}

AUDIO_LIKE = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus"}


def is_direct_media_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    return Path(parsed.path).suffix.lower() in DIRECT_MEDIA_EXTS


def direct_plan(url: str, preset: str) -> MediaPlan:
    parsed = urlparse(url)
    filename = unquote(parsed.path.rsplit("/", 1)[-1]) or "download"
    suffix = Path(filename).suffix.lower()
    source_ext = suffix.lstrip(".") or "bin"

    stream = Stream(
        url=url,
        protocol=parsed.scheme,
        ext=source_ext,
        format_id="direct",
    )
    audio_only = suffix in AUDIO_LIKE
    if preset == "audio" or audio_only:
        return MediaPlan(
            title=Path(filename).stem or "audio",
            preset="audio" if audio_only else preset,
            mode="audio",
            ext="mp3",
            webpage_url=url,
            engine="mydl",
            video=None if audio_only else stream,
            audio=stream if audio_only else None,
            direct=True,
        )
    return MediaPlan(
        title=Path(filename).stem or "download",
        preset=preset,
        mode="progressive",
        ext=source_ext,
        webpage_url=url,
        engine="mydl",
        video=stream,
        direct=True,
    )
