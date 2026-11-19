from datetime import datetime, timezone
from uuid import uuid4

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


TERMINAL_STATUSES = {"done", "failed", "expired"}
RUNNING_STATUSES = {"extracting", "downloading", "processing", "uploading"}
ACTIVE_STATUSES = {"queued", *RUNNING_STATUSES}


class User(SQLModel, table=True):
    id: str = Field(primary_key=True, default_factory=lambda: uuid4().hex)
    email: str = Field(index=True, unique=True)
    password_hash: str
    plan: str = "free"
    status: str = "active"
    created_at: datetime = Field(default_factory=utcnow, index=True)


class AuthSession(SQLModel, table=True):
    token_hash: str = Field(primary_key=True)
    user_id: str = Field(index=True, foreign_key="user.id")
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime = Field(index=True)
    ip_hash: str | None = None
    user_agent: str | None = None


class AgentPair(SQLModel, table=True):
    """One-time pairing token handed from the web app to the desktop companion."""

    token_hash: str = Field(primary_key=True)
    user_id: str = Field(index=True, foreign_key="user.id")
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime = Field(index=True)


class AgentDevice(SQLModel, table=True):
    """Long-lived token the desktop companion stores locally after pairing."""

    id: str = Field(primary_key=True, default_factory=lambda: uuid4().hex)
    token_hash: str = Field(index=True, unique=True)
    user_id: str = Field(index=True, foreign_key="user.id")
    label: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    last_seen_at: datetime | None = None


class DesktopJob(SQLModel, table=True):
    """Server-side record of a download executed by the desktop companion.

    Rows exist only for metering (hourly quota, concurrency, abuse stats); the
    media never reaches the server. ``id`` is ``<device_id>:<client job id>`` so
    repeated reserve/report calls are idempotent per device.
    """

    id: str = Field(primary_key=True)
    job_id: str = Field(index=True)
    user_id: str = Field(index=True, foreign_key="user.id")
    device_id: str = Field(index=True, foreign_key="agentdevice.id")
    preset: str = "best"
    status: str = "running"
    size_bytes: int = 0
    host: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow, index=True)
    finished_at: datetime | None = None


class Job(SQLModel, table=True):
    id: str = Field(primary_key=True)
    url: str
    preset: str = "best"
    user_id: str | None = Field(default=None, index=True, foreign_key="user.id")
    client_hash: str | None = Field(default=None, index=True)
    cookies_enc: str | None = None

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
    worker_id: str | None = None

    created_at: datetime = Field(default_factory=utcnow, index=True)
    started_at: datetime | None = None
    finished_at: datetime | None = None
