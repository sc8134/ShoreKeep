from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    public_base_url: str = "http://127.0.0.1:8000"
    retention_seconds: int = 3600
    max_filesize_mb: int = 2048
    job_timeout_seconds: int = 1800
    max_queue_depth: int = 50
    playlist_max_items: int = 20
    playlist_max_total_mb: int = 4096
    subtitle_max_kb: int = 2048
    mydl_connections: int = 8
    ffmpeg_bin: str = ""
    worker_poll_seconds: float = 1.0
    storage_backend: str = "local"

    # Accounts and sessions
    secret_key: str = "dev-insecure-change-me"
    session_days: int = 30
    cookie_secure: bool = False

    # Plans: anonymous visitors vs signed-in free users (pro reserved for billing)
    anon_jobs_per_ip_hour: int = 5
    free_jobs_per_user_hour: int = 20
    pro_jobs_per_user_hour: int = 100
    anon_max_active_jobs: int = 1
    free_max_active_jobs: int = 2
    pro_max_active_jobs: int = 4
    anon_max_preset: str = "720p"

    # Cloudflare Turnstile (disabled unless enabled and a secret is set)
    turnstile_enabled: bool = False
    turnstile_site_key: str = ""
    turnstile_secret: str = ""

    max_cookie_kb: int = 256

    # Desktop companion pairing and usage records
    agent_pair_ttl_seconds: int = 300
    desktop_usage_retention_days: int = 30

    r2_bucket: str = ""
    r2_endpoint: str = ""
    r2_access_key: str = ""
    r2_secret_key: str = ""

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"

    @property
    def max_filesize_bytes(self) -> int:
        return self.max_filesize_mb * 1024 * 1024

    @property
    def playlist_max_total_bytes(self) -> int:
        return self.playlist_max_total_mb * 1024 * 1024


settings = Settings()
