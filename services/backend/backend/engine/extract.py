from __future__ import annotations

import yt_dlp

from .types import ExtractionError, MediaPlan, Stream

VIDEO_CAPS: dict[str, int | None] = {"best": None, "1080p": 1080, "720p": 720, "480p": 480, "audio": None}
VIDEO_PREFERENCE = ("mp4", "m4v")


def extract_info(
    url: str,
    cookiefile: str | None = None,
    playlist: bool = False,
    limit: int | None = None,
) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": not playlist,
        "extract_flat": "in_playlist" if playlist else False,
        "socket_timeout": 30,
        "cachedir": False,
    }
    if playlist:
        # Enumerate entries cheaply first; each entry is extracted fully when downloaded.
        opts["ignoreerrors"] = True
        if limit:
            opts["playlistend"] = limit
    if cookiefile:
        opts["cookiefile"] = cookiefile
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # yt-dlp raises DownloadError and friends
        raise ExtractionError(_clean_error(str(exc))) from exc
    if not info:
        raise ExtractionError("No media information could be extracted from this URL")
    return info


def build_plan(info: dict, preset: str, max_bytes: int = 0) -> MediaPlan:
    _check_supported(info)
    formats = _usable_formats(info)

    title = info.get("title") or "download"
    webpage_url = info.get("webpage_url") or info.get("original_url") or ""
    duration = info.get("duration")
    thumbnail = info.get("thumbnail")

    if preset == "audio":
        stream = _pick_audio(formats, max_bytes)
        return MediaPlan(
            title=title,
            preset="audio",
            mode="audio",
            ext="mp3",
            webpage_url=webpage_url,
            engine="ytdlp" if stream.fragmented else "mydl",
            selector=stream.format_id,
            audio=stream,
            duration=duration,
            thumbnail=thumbnail,
        )

    cap = VIDEO_CAPS[preset]
    progressive, video_only = _quality_pools(formats, cap, max_bytes)
    hmax = max((f.get("height") or 0) for f in progressive + video_only)

    progressive_at_best = [f for f in progressive if (f.get("height") or 0) == hmax]
    if progressive_at_best:
        stream = _stream(max(progressive_at_best, key=_video_key))
        return MediaPlan(
            title=title,
            preset=preset,
            mode="progressive",
            ext=stream.ext or "mp4",
            webpage_url=webpage_url,
            engine="ytdlp" if stream.fragmented else "mydl",
            selector=stream.format_id,
            video=stream,
            duration=duration,
            thumbnail=thumbnail,
        )

    video = _stream(max(video_only, key=_video_key))
    audio = _pick_audio(formats, max_bytes)
    engine = "ytdlp" if (video.fragmented or audio.fragmented) else "mydl"
    return MediaPlan(
        title=title,
        preset=preset,
        mode="merge",
        ext=_container_ext(video, audio),
        webpage_url=webpage_url,
        engine=engine,
        selector=f"{video.format_id}+{audio.format_id}",
        video=video,
        audio=audio,
        duration=duration,
        thumbnail=thumbnail,
    )


def _check_supported(info: dict) -> None:
    if info.get("_type") in {"playlist", "multi_video"} or info.get("entries"):
        raise ExtractionError(
            "This URL points to a playlist that could not be expanded. Paste a single video URL."
        )
    if info.get("is_live"):
        raise ExtractionError("Live streams are not supported yet.")
    if info.get("_has_drm") or any(f.get("has_drm") for f in info.get("formats") or []):
        raise ExtractionError("DRM-protected content is not supported.")


def _usable_formats(info: dict) -> list[dict]:
    formats = []
    for fmt in info.get("formats") or []:
        if not fmt.get("url"):
            continue
        if not _has_video(fmt) and not _has_audio(fmt):
            continue
        formats.append(fmt)
    if not formats:
        raise ExtractionError("No downloadable formats were found for this URL")
    return formats


def _has_video(fmt: dict) -> bool:
    return fmt.get("vcodec") not in (None, "none")


def _has_audio(fmt: dict) -> bool:
    codec = fmt.get("acodec")
    if codec not in (None, "none"):
        return True
    # HLS audio renditions often omit acodec and mark vcodec as "none".
    return codec is None and fmt.get("vcodec") == "none"


def _size(fmt: dict) -> int | None:
    return fmt.get("filesize") or fmt.get("filesize_approx")


def _within(fmt: dict, max_bytes: int) -> bool:
    size = _size(fmt)
    return not max_bytes or not size or size <= max_bytes


def _quality_pools(formats: list[dict], cap: int | None, max_bytes: int) -> tuple[list[dict], list[dict]]:
    def quality_ok(fmt: dict) -> bool:
        return cap is None or (fmt.get("height") or 0) <= cap

    progressive = [f for f in formats if _has_video(f) and _has_audio(f) and quality_ok(f) and _within(f, max_bytes)]
    video_only = [f for f in formats if _has_video(f) and not _has_audio(f) and quality_ok(f) and _within(f, max_bytes)]
    if progressive or video_only:
        return progressive, video_only

    videos = [f for f in formats if _has_video(f) and _within(f, max_bytes)]
    if not videos:
        raise ExtractionError("No video format fits under the configured size limit")
    lowest = min((f.get("height") or 0) for f in videos)
    return (
        [f for f in videos if _has_audio(f) and (f.get("height") or 0) == lowest],
        [f for f in videos if not _has_audio(f) and (f.get("height") or 0) == lowest],
    )


def _pick_audio(formats: list[dict], max_bytes: int) -> Stream:
    candidates = [f for f in formats if _has_audio(f) and not _has_video(f) and _within(f, max_bytes)]
    if not candidates:
        candidates = [f for f in formats if _has_audio(f) and _within(f, max_bytes)]
    if not candidates:
        raise ExtractionError("No audio track fits under the configured size limit")
    # yt-dlp lists formats from lowest to highest quality; later wins ties
    # because HLS renditions often carry no bitrate metadata.
    best = candidates[0]
    for fmt in candidates[1:]:
        if _audio_key(fmt) >= _audio_key(best):
            best = fmt
    return _stream(best)


def _pref_ext(fmt: dict) -> int:
    return 1 if (fmt.get("ext") or "") in VIDEO_PREFERENCE else 0


def _video_key(fmt: dict) -> tuple[int, float]:
    return _pref_ext(fmt), float(fmt.get("tbr") or 0)


def _audio_key(fmt: dict) -> tuple[float, int]:
    return float(fmt.get("abr") or fmt.get("tbr") or 0), _pref_ext(fmt)


def _container_ext(video: Stream, audio: Stream) -> str:
    vext, aext = (video.ext or "").lower(), (audio.ext or "").lower()
    if vext in {"mp4", "m4v"} and aext in {"m4a", "mp4"}:
        return "mp4"
    if vext == "webm" and aext == "webm":
        return "webm"
    return "mkv"


def _stream(fmt: dict) -> Stream:
    return Stream(
        url=fmt["url"],
        protocol=fmt.get("protocol") or "",
        ext=fmt.get("ext") or "",
        format_id=str(fmt.get("format_id") or ""),
        vcodec=fmt.get("vcodec"),
        acodec=fmt.get("acodec"),
        filesize=_size(fmt),
        height=fmt.get("height"),
        tbr=fmt.get("tbr"),
        http_headers=dict(fmt.get("http_headers") or {}),
    )


def _clean_error(message: str) -> str:
    text = message.strip()
    for prefix in ("ERROR: ", "error: "):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text or "Extraction failed"
