from __future__ import annotations

import importlib.util
import logging
import os
import subprocess
import sys
import webbrowser
from pathlib import Path

from . import webapp
from .config import AgentConfig, config_dir
from .platform_utils import SingleInstance

logger = logging.getLogger(__name__)

WINDOW_TITLE = "Shorekeep"
_instance: SingleInstance | None = None


def available() -> bool:
    return importlib.util.find_spec("webview") is not None


def _pythonw() -> str:
    exe = Path(sys.executable)
    if exe.name.lower() == "python.exe":
        candidate = exe.with_name("pythonw.exe")
        if candidate.exists():
            return str(candidate)
    return str(exe)


def _command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--window"]
    return [_pythonw(), "-m", "mydl_desktop.window"]


def spawn(config: AgentConfig) -> subprocess.Popen | None:
    """Open the native window in its own process so the tray keeps running when it closes."""
    if not available():
        return None
    log = open(config_dir() / "window.log", "a", encoding="utf-8")
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(
        _command(),
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=log,
        cwd=str(config_dir()),
        env=dict(os.environ),
        creationflags=creationflags,
        close_fds=True,
    )


def _acquire_single_instance() -> bool:
    global _instance
    if _instance is None:
        _instance = SingleInstance("window")
    return _instance.acquire()


def _gui() -> str:
    override = os.environ.get("SHOREKEEP_WEBVIEW_GUI")
    if override:
        return override
    if sys.platform == "win32":
        return "edgechromium"
    if sys.platform == "darwin":
        return "cocoa"
    return "gtk"


def main(config: AgentConfig) -> int:
    logging.basicConfig(
        level=logging.INFO,
        handlers=[logging.FileHandler(config_dir() / "window.log", encoding="utf-8")],
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not _acquire_single_instance():
        return 0
    if not webapp.wait_ready(config, timeout=20.0):
        logger.warning("web app is not responding; opening the window anyway")
    if not available():
        webbrowser.open(config.server_base)
        return 0

    try:
        import webview

        # WebView2 cancels downloads by default, which broke the "Download" button
        # in the finished-job popup even though it works in a regular browser.
        webview.settings["ALLOW_DOWNLOADS"] = True

        webview.create_window(
            WINDOW_TITLE,
            config.server_base,
            width=1280,
            height=860,
            min_size=(900, 600),
        )
        webview.start(gui=_gui())
    except Exception:
        logger.exception("the native window failed to start")
        return 1
    return 0
