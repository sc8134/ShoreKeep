from datetime import datetime, timezone
from uuid import uuid4

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


TERMINAL_STATUSES = {"done", "failed", "expired"}
RUNNING_STATUSES = {"extracting", "downloading", "processing", "uploading"}
ACTIVE_STATUSES = {"queued", "paused", *RUNNING_STATUSES}


class User(SQLModel, table=True):
    id: str = Field(primary_key=True, default_factory=lambda: uuid4().hex)
    email: str = Field(index=True, unique=True)
    password_hash: str
    plan: str = "free"
    status: str = "active"
    created_at: datetime = Field(default_factory=utcnow, index=True)
    # Email verification
    email_verified: bool = Field(default=False)
    email_verify_token_hash: str | None = Field(default=None, index=True)
    email_verify_expires_at: datetime | None = None
    # Password reset
    reset_token_hash: str | None = Field(default=None, index=True)
    reset_token_expires_at: datetime | None = None


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


class Subscription(SQLModel, table=True):
    """Current billing subscription per user, mirrored from Paddle webhooks."""

    user_id: str = Field(primary_key=True, foreign_key="user.id")
    provider: str = "paddle"
    customer_id: str | None = Field(default=None, index=True)
    subscription_id: str | None = Field(default=None, index=True, unique=True)
    price_id: str | None = None
    interval: str | None = None
    status: str = "inactive"
    current_period_end: datetime | None = None
    cancel_at_period_end: bool = False
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class BillingEvent(SQLModel, table=True):
    """Processed webhook ids, so Paddle retries are idempotent."""

    event_id: str = Field(primary_key=True)
    event_type: str = Field(index=True)
    user_id: str | None = Field(default=None, index=True)
    received_at: datetime = Field(default_factory=utcnow, index=True)


class BandwidthSettings(SQLModel, table=True):
    """Persisted global bandwidth limit. Single row keyed by id=1."""

    id: int = Field(primary_key=True, default=1)
    limit_kbps: float = Field(default=0.0)   # 0 = unlimited
    updated_at: datetime = Field(default_factory=utcnow)


class CookieProfile(SQLModel, table=True):
    """Named, encrypted cookies.txt stored per user for reuse across jobs."""

    id: str = Field(primary_key=True, default_factory=lambda: uuid4().hex)
    user_id: str = Field(index=True, foreign_key="user.id")
    name: str = Field(max_length=64)
    domain: str = Field(max_length=253)
    cookies_enc: str
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Job(SQLModel, table=True):
    id: str = Field(primary_key=True)
    url: str
    preset: str = "best"
    user_id: str | None = Field(default=None, index=True, foreign_key="user.id")
    client_hash: str | None = Field(default=None, index=True)
    cookies_enc: str | None = None
    want_subtitles: bool = False
    max_filesize_mb: int | None = None

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
    chapters: str | None = None

    playlist_index: int | None = None
    playlist_count: int | None = None

    error: str | None = None
    error_code: str | None = None
    worker_id: str | None = None

    # Priority and scheduling
    priority: int = Field(default=0)   # 0 = normal, 1 = high (Pro only)
    run_after: datetime | None = None  # if set, worker skips until now >= run_after

    # Playlist per-item children
    parent_job_id: str | None = Field(default=None, index=True, foreign_key="job.id")
    item_index: int | None = None

    # Phase 1 (power controls): per-job bandwidth cap and auto-retry
    bandwidth_limit_kbps: float = Field(default=0.0)  # 0 = use global / unlimited
    retry_count: int = Field(default=0)
    max_retries: int = Field(default=0)  # 0 = no auto-retry

    created_at: datetime = Field(default_factory=utcnow, index=True)
    started_at: datetime | None = None
    finished_at: datetime | None = None
