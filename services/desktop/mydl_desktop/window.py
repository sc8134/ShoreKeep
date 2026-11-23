from __future__ import annotations

import importlib.util
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from . import webapp
from .config import AGENT_HOST, AGENT_PORT, AgentConfig, config_dir
from .control import FocusChannel, request_focus
from .platform_utils import SingleInstance

logger = logging.getLogger(__name__)

WINDOW_TITLE = "Shorekeep"
AGENT_BASE = f"http://{AGENT_HOST}:{AGENT_PORT}"
_instance: SingleInstance | None = None


def _focus_native() -> bool:
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            user32.GetWindowThreadProcessId.argtypes = [
                wintypes.HWND,
                ctypes.POINTER(wintypes.DWORD),
            ]
            user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.SetForegroundWindow.argtypes = [wintypes.HWND]
            user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
            user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
            user32.IsWindowVisible.argtypes = [wintypes.HWND]

            pid = os.getpid()
            found: list[int] = []
            callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def visit(handle, _lparam):
                owner = wintypes.DWORD()
                user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
                if owner.value != pid or not user32.IsWindowVisible(handle):
                    return True
                length = user32.GetWindowTextLengthW(handle)
                if length <= 0:
                    return True
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(handle, buffer, length + 1)
                if buffer.value.startswith(WINDOW_TITLE):
                    found.append(handle)
                    return False
                return True

            user32.EnumWindows(callback_type(visit), 0)
            if not found:
                return False
            handle = found[0]
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
        ["xdotool", "search", "--name", f"^{WINDOW_TITLE}", "windowactivate"],
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


class _TitleWatcher:
    """Keeps the native title showing live download progress from the local agent."""

    def __init__(self, window, interval: float = 3.0) -> None:
        self.window = window
        self.interval = interval
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._loop, name="window-title", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            title = self._title()
            try:
                if title and self.window.title != title:
                    self.window.title = title
            except Exception:
                logger.debug("could not update the window title", exc_info=True)

    def _title(self) -> str:
        try:
            with urllib.request.urlopen(f"{AGENT_BASE}/health", timeout=1.5) as response:
                info = json.load(response)
        except (urllib.error.URLError, OSError, ValueError):
            return WINDOW_TITLE
        jobs = info.get("active_jobs") or []
        if jobs:
            percent = round(
                sum(float(job.get("progress") or 0) for job in jobs) / len(jobs)
            )
            if len(jobs) == 1:
                name = str(jobs[0].get("title") or "Download").strip()
                if len(name) > 34:
                    name = name[:33] + "…"
                return f"{WINDOW_TITLE} · {percent}% · {name}"
            return f"{WINDOW_TITLE} · {percent}% · {len(jobs)} downloads"
        if info.get("paused"):
            return f"{WINDOW_TITLE} (paused)"
        return WINDOW_TITLE


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
    watcher: _TitleWatcher | None = None
    try:
        import webview

        # WebView2 cancels downloads by default, which broke the "Download" button
        # in the finished-job popup even though it works in a regular browser.
        webview.settings["ALLOW_DOWNLOADS"] = True

        native = webview.create_window(
            WINDOW_TITLE,
            config.server_base,
            width=1280,
            height=860,
            min_size=(900, 600),
        )
        channel.start(focus_existing)
        watcher = _TitleWatcher(native)
        watcher.start()
        webview.start(gui=_gui())
    except Exception:
        logger.exception("the native window failed to start")
        return 1
    finally:
        if watcher is not None:
            watcher.stop()
        channel.close()
    return 0
