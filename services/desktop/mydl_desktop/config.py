from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_DIR_NAME = "Shorekeep"
AGENT_HOST = "127.0.0.1"
AGENT_PORT = 47821
DEFAULT_SERVER = "http://127.0.0.1:8000"
DEFAULT_ORIGINS = ("http://127.0.0.1:8000", "http://localhost:8000")
COOKIE_MODES = ("auto", "firefox", "chrome", "edge", "brave", "off")


def config_dir() -> Path:
    if os.name == "nt" and os.environ.get("APPDATA"):
        base = Path(os.environ["APPDATA"]) / APP_DIR_NAME
    elif os.environ.get("XDG_CONFIG_HOME"):
        base = Path(os.environ["XDG_CONFIG_HOME"]) / APP_DIR_NAME.lower()
    else:
        base = Path.home() / ".config" / APP_DIR_NAME.lower()
    base.mkdir(parents=True, exist_ok=True)
    return base


def default_download_dir() -> Path:
    downloads = Path.home() / "Downloads"
    return downloads / "Shorekeep"


@dataclass
class AgentConfig:
    server_base: str = DEFAULT_SERVER
    device_token: str | None = None
    device_label: str = "Shorekeep Desktop"
    cookie_browser: str = "auto"
    download_dir: str = ""
    allowed_origins: list[str] = field(default_factory=lambda: list(DEFAULT_ORIGINS))
    idle_timeout_minutes: int = 30
    state_dir: str = ""
    hotkey: str = "ctrl+alt+s"
    clipboard_monitor: bool = False
    watch_folder: str = ""

    @property
    def download_path(self) -> Path:
        return Path(os.path.expanduser(self.download_dir)) if self.download_dir else default_download_dir()

    @property
    def state_path(self) -> Path:
        return Path(os.path.expanduser(self.state_dir)) if self.state_dir else config_dir()

    @property
    def config_path(self) -> Path:
        return config_dir() / "config.json"

    @property
    def log_path(self) -> Path:
        return config_dir() / "agent.log"

    def save(self) -> None:
        path = self.config_path
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    @classmethod
    def load(cls) -> "AgentConfig":
        path = config_dir() / "config.json"
        data: dict = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
        known = {f for f in cls.__dataclass_fields__}
        config = cls(**{k: v for k, v in data.items() if k in known})

        if os.environ.get("SHOREKEEP_SERVER"):
            config.server_base = os.environ["SHOREKEEP_SERVER"].rstrip("/")
        if os.environ.get("SHOREKEEP_DOWNLOAD_DIR"):
            config.download_dir = os.environ["SHOREKEEP_DOWNLOAD_DIR"]
        if os.environ.get("SHOREKEEP_ALLOWED_ORIGINS"):
            config.allowed_origins = [
                origin.strip()
                for origin in os.environ["SHOREKEEP_ALLOWED_ORIGINS"].split(",")
                if origin.strip()
            ]
        if os.environ.get("SHOREKEEP_HOTKEY"):
            config.hotkey = os.environ["SHOREKEEP_HOTKEY"]
        if os.environ.get("SHOREKEEP_CLIPBOARD"):
            config.clipboard_monitor = os.environ["SHOREKEEP_CLIPBOARD"].strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
        if os.environ.get("SHOREKEEP_WATCH_FOLDER"):
            config.watch_folder = os.environ["SHOREKEEP_WATCH_FOLDER"]
        if config.cookie_browser not in COOKIE_MODES:
            config.cookie_browser = "auto"
        return config
