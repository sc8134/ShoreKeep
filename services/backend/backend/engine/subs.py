from __future__ import annotations

import logging
import re
import urllib.request
from pathlib import Path
from urllib.parse import urljoin

from ..config import settings
from ..security import UnsafeURLError, assert_public_http_url

logger = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
_LANG_CLEAN = re.compile(r"[^A-Za-z0-9_-]+")


def safe_lang(lang: str) -> str:
    cleaned = _LANG_CLEAN.sub("", lang or "")[:24]
    return cleaned or "subs"


def chapters_from(info: dict) -> list[dict]:
    chapters: list[dict] = []
    for chapter in info.get("chapters") or []:
        start = chapter.get("start_time")
        title = chapter.get("title")
        if start is None or not title:
            continue
        chapters.append({"start": round(float(start), 2), "title": str(title)[:200]})
    return chapters


def format_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def chapters_text(chapters: list[dict]) -> str:
    return "\n".join(
        f"{format_timestamp(chapter['start'])}  {chapter['title']}" for chapter in chapters
    )


def pick_subtitle(info: dict) -> tuple[str, dict] | None:
    """Prefer human-made captions over automatic ones, English first, VTT only."""
    for source in ("subtitles", "automatic_captions"):
        tracks = info.get(source) or {}
        for lang in _lang_order(tracks):
            for fmt in tracks.get(lang) or []:
                if (fmt.get("ext") or "").lower() == "vtt" and fmt.get("url"):
                    return lang, fmt
    return None


def _lang_order(tracks: dict) -> list[str]:
    def rank(lang: str) -> tuple[int, str]:
        lowered = lang.lower()
        if lowered in {"en", "en-us", "en-gb", "en-orig"}:
            return (0, lowered)
        if lowered.startswith("en"):
            return (1, lowered)
        return (2, lowered)

    return sorted(tracks.keys(), key=rank)


def _headers(http_headers: dict[str, str] | None) -> dict[str, str]:
    headers = {
        key: value
        for key, value in (http_headers or {}).items()
        if key.lower() not in {"accept-encoding"}
    }
    headers.setdefault("User-Agent", DEFAULT_UA)
    return headers


def _read(url: str, headers: dict[str, str], limit: int) -> bytes:
    assert_public_http_url(url)
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        final_url = response.geturl()
        if str(final_url) != url:
            assert_public_http_url(str(final_url))
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Subtitle file is larger than the configured limit")
    return data


def _strip_header(text: str) -> str:
    kept = []
    for line in text.replace("\ufeff", "").splitlines():
        stripped = line.strip()
        if stripped.upper() == "WEBVTT" or stripped.upper().startswith("X-TIMESTAMP-MAP"):
            continue
        kept.append(line.rstrip())
    return "\n".join(kept).strip()


def _merge_hls(
    text: str, base_url: str, headers: dict[str, str], limit: int, depth: int = 0
) -> bytes:
    """Turn an HLS subtitle playlist into one sidecar VTT file."""
    lines = text.splitlines()
    segments = [
        line.strip() for line in lines if line.strip() and not line.strip().startswith("#")
    ]
    if not segments:
        raise ValueError("Subtitle playlist has no segments")
    if any(line.strip().startswith("#EXT-X-KEY") for line in lines):
        raise ValueError("Encrypted subtitle playlists are not supported")
    if len(segments) > 5000:
        raise ValueError("Subtitle playlist has too many segments")

    if len(segments) == 1 and segments[0].lower().split("?")[0].endswith(".m3u8"):
        if depth >= 1:
            raise ValueError("Subtitle playlist nests too deeply")
        nested = _read(urljoin(base_url, segments[0]), headers, limit)
        return _merge_hls(nested.decode("utf-8", "replace"), base_url, headers, limit, depth + 1)

    chunks: list[str] = []
    total = 0
    for segment in segments:
        url = urljoin(base_url, segment)
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError("Subtitle playlist uses an unsupported segment URL")
        remaining = limit - total
        if remaining <= 0:
            raise ValueError("Subtitle file is larger than the configured limit")
        data = _read(url, headers, remaining)
        total += len(data)
        body = _strip_header(data.decode("utf-8", "replace"))
        if body:
            chunks.append(body)
    if not chunks:
        raise ValueError("Subtitle playlist contained no text")
    merged = "WEBVTT\n\n" + "\n\n".join(chunks) + "\n"
    if len(merged.encode("utf-8")) > limit:
        raise ValueError("Subtitle file is larger than the configured limit")
    return merged.encode("utf-8")


def download_subtitle(fmt: dict, dest: Path, http_headers: dict[str, str] | None = None) -> Path:
    url = fmt["url"]
    headers = _headers(http_headers)
    limit = settings.subtitle_max_kb * 1024
    data = _read(url, headers, limit)
    if data.lstrip().startswith(b"#EXTM3U"):
        data = _merge_hls(data.decode("utf-8", "replace"), url, headers, limit)
    if not data.strip():
        raise ValueError("Subtitle file was empty")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


def fetch_subtitle(info: dict, work: Path) -> tuple[Path, str] | None:
    """Best-effort subtitle download; subtitle problems never fail a media job."""
    picked = pick_subtitle(info)
    if picked is None:
        return None
    lang, fmt = picked
    clean = safe_lang(lang)
    dest = work / f"subtitles.{clean}.vtt"
    try:
        return download_subtitle(fmt, dest, info.get("http_headers") or {}), clean
    except (UnsafeURLError, OSError, ValueError) as exc:
        logger.info("subtitle download skipped: %s", exc)
        return None
