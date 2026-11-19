from __future__ import annotations

import mimetypes
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..config import settings
from ..models import Job

_UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


@dataclass
class StoredFile:
    name: str
    path: str
    size: int
    mime: str


def safe_download_name(title: str, ext: str) -> str:
    base = _UNSAFE_CHARS.sub(" ", title or "download").strip()
    base = re.sub(r"\s+", " ", base)[:120].rstrip(" .") or "download"
    return f"{base}.{ext}" if ext else base


def _guess_mime(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


class LocalStorage:
    def save(self, src: Path, job_id: str, title: str) -> StoredFile:
        ext = src.suffix.lower() or ".bin"
        dest = settings.media_dir / f"{job_id}{ext}"
        shutil.move(str(src), dest)
        return StoredFile(
            name=safe_download_name(title, ext.lstrip(".")),
            path=str(dest),
            size=dest.stat().st_size,
            mime=_guess_mime(dest),
        )

    def delete(self, job: Job) -> None:
        if job.output_path and not job.output_path.startswith("r2:"):
            Path(job.output_path).unlink(missing_ok=True)

    def presign(self, job: Job) -> str | None:
        return None


class R2Storage:
    def __init__(self) -> None:
        import boto3

        self.client = boto3.client(
            "s3",
            endpoint_url=settings.r2_endpoint,
            aws_access_key_id=settings.r2_access_key,
            aws_secret_access_key=settings.r2_secret_key,
            region_name="auto",
        )

    def save(self, src: Path, job_id: str, title: str) -> StoredFile:
        ext = src.suffix.lower() or ".bin"
        key = f"media/{job_id}{ext}"
        mime = _guess_mime(src)
        size = src.stat().st_size
        self.client.upload_file(
            str(src),
            settings.r2_bucket,
            key,
            ExtraArgs={"ContentType": mime},
        )
        src.unlink(missing_ok=True)
        return StoredFile(
            name=safe_download_name(title, ext.lstrip(".")),
            path=f"r2:{key}",
            size=size,
            mime=mime,
        )

    def delete(self, job: Job) -> None:
        if job.output_path and job.output_path.startswith("r2:"):
            self.client.delete_object(Bucket=settings.r2_bucket, Key=job.output_path[3:])

    def presign(self, job: Job) -> str | None:
        if not job.output_path or not job.output_path.startswith("r2:"):
            return None
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.r2_bucket, "Key": job.output_path[3:]},
            ExpiresIn=settings.retention_seconds,
        )


def _build_storage() -> LocalStorage | R2Storage:
    if settings.storage_backend == "r2":
        return R2Storage()
    return LocalStorage()


storage = _build_storage()
