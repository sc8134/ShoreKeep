from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import webbrowser

from .config import AgentConfig

logger = logging.getLogger(__name__)

_MUTEX_NAME = "Shorekeep-Tray"
_handles: list[int] = []


def acquire_single_instance() -> bool:
    """Return False when another tray instance is already running (Windows only)."""
    if sys.platform != "win32":
        return True
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if not handle:
        return True
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return False
    _handles.append(handle)
    return True


def icon_image(size: int = 256):
    from importlib.resources import files

    from PIL import Image

    resource = files("mydl_desktop") / "assets" / "shorekeep-tray.png"
    with resource.open("rb") as handle:
        image = Image.open(handle).convert("RGBA")
    return image.resize((size, size), Image.LANCZOS)


def _open_path(path) -> None:
    if hasattr(os, "startfile"):
        os.startfile(str(path))
    else:
        webbrowser.open(path.as_uri() if hasattr(path, "as_uri") else str(path))


def run_tray(config: AgentConfig) -> int:
    from .agent import health, start_agent

    child: subprocess.Popen | None = None
    if not health():
        child = start_agent(config)
        if child is None:
            logger.error("the agent did not start; see %s", config.log_path)
    try:
        import pystray
        from pystray import Menu, MenuItem
    except Exception:
        logger.exception("tray support is not installed; the agent keeps running in the background")
        return 0 if health() else 1

    def status_text(_item) -> str:
        info = health()
        if not info:
            return "Agent: starting"
        return "Agent: running" if info.get("paired") else "Agent: running (not paired)"

    def open_folder(_icon, _item) -> None:
        path = config.download_path
        path.mkdir(parents=True, exist_ok=True)
        try:
            _open_path(path)
        except Exception:
            logger.exception("could not open %s", path)

    def open_site(_icon, _item) -> None:
        webbrowser.open(config.server_base)

    def quit_app(icon, _item) -> None:
        icon.stop()

    menu = Menu(
        MenuItem(status_text, None, enabled=False),
        MenuItem("Open download folder", open_folder),
        MenuItem("Open Shorekeep", open_site),
        Menu.SEPARATOR,
        MenuItem("Quit", quit_app),
    )
    icon = pystray.Icon("shorekeep-desktop", icon_image(64), "Shorekeep", menu)
    try:
        icon.run()
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
    return 0
