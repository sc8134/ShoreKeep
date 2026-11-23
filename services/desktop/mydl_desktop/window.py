from __future__ import annotations

import importlib.util
import logging
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from . import webapp
from .config import AgentConfig, config_dir
from .control import FocusChannel, request_focus
from .platform_utils import SingleInstance

logger = logging.getLogger(__name__)

WINDOW_TITLE = "Shorekeep"
_instance: SingleInstance | None = None


def _focus_native() -> bool:
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            user32.FindWindowW.restype = wintypes.HWND
            user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
            user32.GetWindowThreadProcessId.argtypes = [
                wintypes.HWND,
                ctypes.POINTER(wintypes.DWORD),
            ]
            user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.SetForegroundWindow.argtypes = [wintypes.HWND]

            handle = user32.FindWindowW(None, WINDOW_TITLE)
            if not handle:
                return False
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
            if owner.value != os.getpid():
                return False
            user32.ShowWindow(handle, 9)  # SW_RESTORE
            return bool(user32.SetForegroundWindow(handle))
        except Exception:
            logger.exception("could not focus the window through the Win32 API")
            return False
    if sys.platform == "darwin":
        script = (
            'tell application "System Events" to set frontmost of '
            f"(first process whose unix id is {os.getpid()}) to true"
        )
        try:
            result = subprocess.run(
                ["osascript", "-e", script], capture_output=True, timeout=3
            )
            return result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
    commands = (
        ["wmctrl", "-a", WINDOW_TITLE],
        ["xdotool", "search", "--name", f"^{WINDOW_TITLE}$", "windowactivate"],
    )
    for command in commands:
        try:
            result = subprocess.run(command, capture_output=True, timeout=3)
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0:
            return True
    return False


def _focus_webview() -> bool:
    try:
        import webview

        if not webview.windows:
            return False
        target = webview.windows[0]
        target.show()
        try:
            target.restore()
        except Exception:
            pass
        target.on_top = True
        target.on_top = False
        return True
    except Exception:
        logger.exception("could not raise the existing window")
        return False


def focus_existing() -> None:
    if _focus_native():
        return
    _focus_webview()


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
    try:
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
    finally:
        log.close()


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


def focus_running(config: AgentConfig, timeout: float = 2.0) -> bool:
    path = config.state_path / "window.control"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if request_focus(path):
            return True
        time.sleep(0.2)
    return False


def main(config: AgentConfig) -> int:
    logging.basicConfig(
        level=logging.INFO,
        handlers=[logging.FileHandler(config_dir() / "window.log", encoding="utf-8")],
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not _acquire_single_instance():
        if not focus_running(config):
            logger.info("another window is running but did not answer the focus request")
        return 0
    if not webapp.wait_ready(config, timeout=20.0):
        logger.warning("web app is not responding; opening the window anyway")
    if not available():
        webbrowser.open(config.server_base)
        return 0

    channel = FocusChannel(config.state_path / "window.control")
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
        channel.start(focus_existing)
        webview.start(gui=_gui())
    except Exception:
        logger.exception("the native window failed to start")
        return 1
    finally:
        channel.close()
    return 0
