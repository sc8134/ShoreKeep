from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .config import AGENT_HOST, AGENT_PORT, AgentConfig, config_dir

AGENT_BASE = f"http://{AGENT_HOST}:{AGENT_PORT}"

logger = logging.getLogger("mydl_desktop")


def _setup_logging() -> None:
    handlers: list[logging.Handler] = [logging.FileHandler(config_dir() / "agent.log", encoding="utf-8")]
    if sys.stderr is not None and sys.stderr.isatty():
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        handlers=handlers,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def health(timeout: float = 1.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"{AGENT_BASE}/health", timeout=timeout) as response:
            return json.load(response)
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _pythonw() -> str:
    exe = Path(sys.executable)
    if exe.name.lower() == "python.exe":
        candidate = exe.with_name("pythonw.exe")
        if candidate.exists():
            return str(candidate)
    return str(exe)


def _serve_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--serve"]
    return [_pythonw(), "-m", "mydl_desktop.agent", "--serve"]


def start_agent(config: AgentConfig, timeout: float = 12.0) -> subprocess.Popen | None:
    """Spawn the agent when it is not already running. Returns the child process, if any."""
    log = open(config.log_path, "a", encoding="utf-8")
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    logger.info("starting desktop agent on %s", AGENT_BASE)
    try:
        child = subprocess.Popen(
            _serve_command(),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            cwd=str(Path.home()),
            env=dict(os.environ),
            creationflags=creationflags,
            close_fds=True,
        )
    finally:
        log.close()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if health(timeout=0.6):
            return child
        if child.poll() is not None:
            return None
        time.sleep(0.4)
    child.terminate()
    return None


def ensure_agent(config: AgentConfig, timeout: float = 12.0) -> bool:
    if health():
        return True
    return start_agent(config, timeout) is not None


def serve(config: AgentConfig) -> None:
    import uvicorn

    from . import metering
    from .jobs import JobManager
    from .server import create_app, start_idle_watch

    manager = JobManager(config)
    app = create_app(config, manager)
    metering.start_flusher(config)
    start_idle_watch(manager, config.idle_timeout_minutes)
    logger.info("agent serving on %s (downloads: %s)", AGENT_BASE, config.download_path)
    uvicorn.run(app, host=AGENT_HOST, port=AGENT_PORT, log_level="warning", access_log=False)


def handle_deep_link(uri: str, config: AgentConfig) -> int:
    parts = urlsplit(uri)
    if parts.scheme.lower() != "shorekeep":
        logger.error("unsupported URI: %s", uri)
        return 2
    route = parts.netloc.strip("/") or parts.path.strip("/")
    if route not in {"", "run"}:
        logger.error("unknown shorekeep:// route: %s", route)
        return 2

    params = parse_qs(parts.query)
    pair = (params.get("pair") or [""])[0]
    url = (params.get("url") or [""])[0]
    preset = (params.get("preset") or ["best"])[0]
    subtitles = (params.get("subs") or ["0"])[0] in {"1", "true", "yes"}
    job_id = (params.get("job") or [None])[0]
    if not pair or not url:
        logger.error("deep link is missing pair or url: %s", uri)
        return 2

    if not ensure_agent(config):
        logger.error("desktop agent did not start; check %s", config.log_path)
        return 3

    payload = json.dumps(
        {"url": url, "preset": preset, "subtitles": subtitles, "job_id": job_id}
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{AGENT_BASE}/v1/run",
        data=payload,
        headers={"Content-Type": "application/json", "X-Agent-Key": pair},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            job = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        logger.error("agent rejected the job (%s): %s", exc.code, detail)
        return 4
    except Exception:
        logger.error("failed to start job:\n%s", traceback.format_exc())
        return 5

    logger.info("desktop download started: %s (%s)", job.get("id"), url)
    return 0


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    parser = argparse.ArgumentParser(prog="shorekeep-desktop", description="Shorekeep desktop companion")
    parser.add_argument("uri", nargs="?", help="shorekeep:// deep link")
    parser.add_argument("--serve", action="store_true", help="run the local agent server")
    parser.add_argument("--register", action="store_true", help="register the shorekeep:// protocol for this user")
    parser.add_argument("--unregister", action="store_true", help="remove the shorekeep:// protocol")
    parser.add_argument("--status", action="store_true", help="print agent status")
    parser.add_argument("--pair", metavar="TOKEN", help="claim a pairing token from the web app")
    args = parser.parse_args(argv)
    config = AgentConfig.load()

    if args.register:
        from .register import install

        install()
        print("Registered shorekeep:// links for the current user.")
        return 0
    if args.unregister:
        from .register import uninstall

        uninstall()
        print("Removed shorekeep:// links.")
        return 0
    if args.status:
        current = health()
        print(json.dumps(current, indent=2) if current else "Agent is not running.")
        return 0
    if args.pair:
        if not ensure_agent(config):
            print("The desktop agent could not be started.", file=sys.stderr)
            return 1
        request = urllib.request.Request(
            f"{AGENT_BASE}/v1/me",
            headers={"X-Agent-Key": args.pair},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                identity = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            print(f"Pairing failed ({exc.code}): {detail}", file=sys.stderr)
            return 4
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(f"Pairing failed: {exc}", file=sys.stderr)
            return 5
        print(json.dumps(identity, indent=2))
        return 0
    if args.serve:
        serve(config)
        return 0
    if args.uri:
        return handle_deep_link(args.uri, config)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
