from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..config import settings


def resolve_ffmpeg() -> str | None:
    if settings.ffmpeg_bin:
        return settings.ffmpeg_bin
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _run(args: list[str]) -> None:
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg is not available on this worker")
    process = subprocess.run(
        [ffmpeg, *args],
        capture_output=True,
        text=True,
        timeout=900,
    )
    if process.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {process.stderr.strip()[-600:]}")


def merge_streams(video: Path, audio: Path, out: Path) -> Path:
    args = [
        "-y",
        "-i", str(video),
        "-i", str(audio),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c", "copy",
    ]
    if out.suffix.lower() == ".mp4":
        args += ["-movflags", "+faststart"]
    args.append(str(out))
    _run(args)
    return out


def to_mp3(source: Path, out: Path) -> Path:
    _run([
        "-y",
        "-i", str(source),
        "-vn",
        "-c:a", "libmp3lame",
        "-b:a", "192k",
        str(out),
    ])
    return out
