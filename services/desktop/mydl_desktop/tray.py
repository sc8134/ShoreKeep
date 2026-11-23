from __future__ import annotations

import logging
import subprocess
import webbrowser

from .config import AgentConfig
from .platform_utils import SingleInstance, open_path

logger = logging.getLogger(__name__)

_instance: SingleInstance | None = None


def acquire_single_instance() -> bool:
    """Return False when another tray instance is already running."""
    global _instance
    if _instance is None:
        _instance = SingleInstance("tray")
    return _instance.acquire()


def icon_image(size: int = 256):
    from importlib.resources import files

    from PIL import Image

    resource = files("mydl_desktop") / "assets" / "shorekeep-tray.png"
    with resource.open("rb") as handle:
        image = Image.open(handle).convert("RGBA")
    return image.resize((size, size), Image.LANCZOS)


def _stop(child: subprocess.Popen | None) -> None:
    if child is None or child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()


def run_tray(
    config: AgentConfig,
    web_child: subprocess.Popen | None = None,
    window_child: subprocess.Popen | None = None,
) -> int:
    from . import window
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
            open_path(path)
        except Exception:
            logger.exception("could not open %s", path)

    def open_site(_icon, _item) -> None:
        nonlocal window_child
        if window_child is None or window_child.poll() is not None:
            window_child = window.spawn(config)
        if window_child is None:
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
        _stop(window_child)
        _stop(child)
        _stop(web_child)
    return 0
