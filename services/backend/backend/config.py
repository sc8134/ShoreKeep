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

    # Database: empty DATABASE_URL keeps the local SQLite file; set a
    # postgresql+psycopg:// URL for multi-process production deployments.
    database_url: str = ""
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_recycle: int = 1800
    # Create tables directly instead of running Alembic (throwaway databases only).
    db_auto_create: bool = False

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

    # Billing: Paddle Billing (merchant of record, international cards)
    paddle_env: str = "sandbox"
    paddle_api_key: str = ""
    paddle_client_token: str = ""
    paddle_webhook_secret: str = ""
    paddle_price_pro_monthly: str = ""
    paddle_price_pro_yearly: str = ""
    pro_price_monthly_usd: int = 5
    pro_price_yearly_usd: int = 45
    billing_currency: str = "USD"
    # Pro perk: larger per-file cap than the free tier
    pro_max_filesize_mb: int = 4096

    # Public demo: caps every visitor and turns off billing and cookies.
    demo_mode: bool = False
    demo_max_preset: str = "480p"
    demo_max_filesize_mb: int = 200
    demo_jobs_per_hour: int = 5
    demo_max_active_jobs: int = 1

    r2_bucket: str = ""
    r2_endpoint: str = ""
    r2_access_key: str = ""
    r2_secret_key: str = ""

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def sqlalchemy_url(self) -> str:
        url = (self.database_url or "").strip()
        if not url:
            return f"sqlite:///{self.db_path}"
        # Hosted providers hand out postgres:// URLs; use the psycopg 3 driver.
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix) :]
        return url

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

    @property
    def pro_max_filesize_bytes(self) -> int:
        return self.pro_max_filesize_mb * 1024 * 1024

    @property
    def paddle_api_base(self) -> str:
        if self.paddle_env == "production":
            return "https://api.paddle.com"
        return "https://sandbox-api.paddle.com"

    @property
    def billing_enabled(self) -> bool:
        return bool(
            not self.demo_mode
            and self.paddle_api_key
            and self.paddle_client_token
            and self.paddle_webhook_secret
            and self.paddle_price_pro_monthly
        )


settings = Settings()
