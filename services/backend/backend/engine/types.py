from __future__ import annotations

from dataclasses import dataclass, field


class ExtractionError(RuntimeError):
    pass


class SizeLimitExceeded(RuntimeError):
    pass


class JobTimeout(RuntimeError):
    pass


class PlaylistEmpty(RuntimeError):
    pass


@dataclass
class Stream:
    url: str
    protocol: str
    ext: str
    format_id: str = ""
    vcodec: str | None = None
    acodec: str | None = None
    filesize: int | None = None
    height: int | None = None
    tbr: float | None = None
    http_headers: dict[str, str] = field(default_factory=dict)

    @property
    def fragmented(self) -> bool:
        protocol = self.protocol or ""
        return (
            protocol in {"m3u8", "m3u8_native", "http_dash_segments", "dash", "dash_segments"}
            or "m3u8" in protocol
            or "dash" in protocol
        )


@dataclass
class MediaPlan:
    title: str
    preset: str
    mode: str  # progressive | merge | audio
    ext: str  # expected output container
    webpage_url: str
    engine: str  # mydl | ytdlp
    selector: str = ""
    video: Stream | None = None
    audio: Stream | None = None
    duration: float | None = None
    thumbnail: str | None = None
    direct: bool = False
