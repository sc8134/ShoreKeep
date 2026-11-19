from __future__ import annotations

import json
import logging
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from .config import AGENT_HOST, AgentConfig, config_dir

logger = logging.getLogger(__name__)

WEB_PORT = 8000
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _target(config: AgentConfig) -> tuple[str, int]:
    parts = urlsplit(config.server_base)
    host = parts.hostname or AGENT_HOST
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return host, port


def is_local(config: AgentConfig) -> bool:
    host, _ = _target(config)
    return host.lower() in LOCAL_HOSTS


def web_ready(config: AgentConfig, timeout: float = 1.0) -> bool:
    try:
        request = urllib.request.Request(
            f"{config.server_base}/v1/health",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
        return data.get("status") == "ok"
    except (urllib.error.URLError, OSError, ValueError):
        return False


def wait_ready(config: AgentConfig, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if web_ready(config):
            return True
        time.sleep(0.4)
    return False


def _pythonw() -> str:
    exe = Path(sys.executable)
    if exe.name.lower() == "python.exe":
        candidate = exe.with_name("pythonw.exe")
        if candidate.exists():
            return str(candidate)
    return str(exe)


def _serve_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--web"]
    return [_pythonw(), "-m", "mydl_desktop.webapp"]


def start_webapp(config: AgentConfig, timeout: float = 30.0) -> subprocess.Popen | None:
    """Start the local web app when it is not already serving. Returns the child process, if any."""
    log = open(config_dir() / "web.log", "a", encoding="utf-8")
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    logger.info("starting the Shorekeep web app on %s", config.server_base)
    child = subprocess.Popen(
        _serve_command(),
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=log,
        cwd=str(config_dir()),
        env=dict(os.environ),
        creationflags=creationflags,
        close_fds=True,
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if web_ready(config, timeout=0.6):
            return child
        if child.poll() is not None:
            return None
        time.sleep(0.4)
    child.terminate()
    return None


def ensure_webapp(config: AgentConfig) -> subprocess.Popen | None:
    """Return None when the web app is already serving (or hosted remotely)."""
    if not is_local(config):
        return None
    if web_ready(config):
        return None
    return start_webapp(config)


def _prepare_environment(config: AgentConfig) -> None:
    if not getattr(sys, "frozen", False):
        return
    state = config_dir()
    os.environ.setdefault("DATA_DIR", str(state / "web"))
    os.environ.setdefault("PUBLIC_BASE_URL", config.server_base.rstrip("/"))
    os.environ.setdefault("COOKIE_SECURE", "false")
    if not os.environ.get("SECRET_KEY"):
        secret_file = state / "web.secret"
        if secret_file.exists():
            os.environ["SECRET_KEY"] = secret_file.read_text(encoding="utf-8").strip()
        else:
            key = secrets.token_urlsafe(48)
            secret_file.write_text(key, encoding="utf-8")
            try:
                os.chmod(secret_file, 0o600)
            except OSError:
                pass
            os.environ["SECRET_KEY"] = key


def _start_worker() -> None:
    from backend.engine import worker

    thread = threading.Thread(target=worker.main, name="web-worker", daemon=True)
    thread.start()


def serve(config: AgentConfig) -> int:
    logging.basicConfig(
        level=logging.INFO,
        handlers=[logging.FileHandler(config_dir() / "web.log", encoding="utf-8")],
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    import uvicorn

    _prepare_environment(config)

    from backend.api.main import app

    _start_worker()
    host, port = _target(config)
    logger.info("serving the Shorekeep web app on http://%s:%s", host, port)
    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)
    return 0


def main() -> int:
    return serve(AgentConfig.load())


if __name__ == "__main__":
    sys.exit(main())
