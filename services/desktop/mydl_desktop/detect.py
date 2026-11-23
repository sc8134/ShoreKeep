from __future__ import annotations

import ctypes
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
WATCH_SUFFIXES = {".txt", ".url", ".webloc"}
MAX_SOURCE_BYTES = 256 * 1024
MAX_SEEN_FILES = 500
CLIPBOARD_INTERVAL = 1.5
FOLDER_INTERVAL = 2.0


def extract_url(text: str) -> str:
    match = URL_RE.search(text or "")
    if not match:
        return ""
    return match.group(0).rstrip("),.;]}\"'")


def read_link_file(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_SOURCE_BYTES:
            return ""
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if path.suffix.lower() == ".url":
        for line in text.splitlines():
            if line.strip().lower().startswith("url="):
                return extract_url(line.split("=", 1)[1])
    return extract_url(text)


def clipboard_available() -> bool:
    if os.name == "nt":
        return True
    if sys.platform == "darwin":
        return shutil.which("pbpaste") is not None
    return any(shutil.which(command) for command in ("wl-paste", "xclip", "xsel"))


def _windows_clipboard() -> str:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    user32.GetClipboardData.restype = ctypes.c_void_p
    if not user32.OpenClipboard(None):
        return ""
    try:
        handle = user32.GetClipboardData(13)  # CF_UNICODETEXT
        if not handle:
            return ""
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return ""
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def clipboard_text() -> str:
    try:
        if os.name == "nt":
            return _windows_clipboard()
        if sys.platform == "darwin":
            commands = (["pbpaste"],)
        else:
            commands = (
                ["wl-paste", "--no-newline"],
                ["xclip", "-selection", "clipboard", "-o"],
                ["xsel", "--clipboard", "--output"],
            )
        for command in commands:
            try:
                result = subprocess.run(command, capture_output=True, timeout=2)
            except (OSError, subprocess.SubprocessError):
                continue
            if result.returncode == 0:
                return result.stdout.decode("utf-8", "replace")
    except Exception:
        logger.debug("could not read the clipboard", exc_info=True)
    return ""


class DetectionStore:
    """Holds the most recent detected link until a page reviews it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict | None = None
        self._counter = 0

    def set(self, url: str, source: str = "") -> dict:
        with self._lock:
            self._counter += 1
            self._pending = {
                "id": f"detected-{self._counter}",
                "url": url,
                "source": (source or "").strip()[:40],
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            return dict(self._pending)

    def current(self) -> dict | None:
        with self._lock:
            return dict(self._pending) if self._pending else None

    def clear(self, detected_id: str) -> bool:
        with self._lock:
            if self._pending and self._pending.get("id") == detected_id:
                self._pending = None
                return True
            return False


class ClipboardWatcher(threading.Thread):
    """Polls the clipboard for newly copied links. Never downloads anything."""

    def __init__(self, on_link: Callable[[str, str], None], interval: float = CLIPBOARD_INTERVAL) -> None:
        super().__init__(name="clipboard-watch", daemon=True)
        self._on_link = on_link
        self.interval = interval
        self._stop = threading.Event()
        self._last = ""

    def run(self) -> None:
        if not clipboard_available():
            logger.warning("clipboard link detection is not available; install xclip or wl-clipboard")
            return
        self._last = clipboard_text()
        while not self._stop.wait(self.interval):
            text = clipboard_text()
            if not text or text == self._last:
                continue
            self._last = text
            url = extract_url(text)
            if not url:
                continue
            try:
                self._on_link(url, "clipboard")
            except Exception:
                logger.exception("clipboard link callback failed")

    def stop(self) -> None:
        self._stop.set()


class FolderWatcher(threading.Thread):
    """Scans a folder for new .txt, .url, and .webloc files that contain links."""

    def __init__(
        self,
        folder: Path,
        on_link: Callable[[str, str], None],
        state_path: Path,
        interval: float = FOLDER_INTERVAL,
    ) -> None:
        super().__init__(name="folder-watch", daemon=True)
        self.folder = folder
        self._on_link = on_link
        self._state_path = state_path
        self.interval = interval
        self._stop = threading.Event()
        self._seen = self._load()

    def _load(self) -> dict[str, str]:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        files = data.get("files") if isinstance(data, dict) else None
        if not isinstance(files, dict):
            return {}
        return {str(name): str(signature) for name, signature in files.items()}

    def _save(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._state_path.with_name(self._state_path.name + ".tmp")
            temporary.write_text(
                json.dumps({"version": 1, "files": self._seen}, indent=1),
                encoding="utf-8",
            )
            os.replace(temporary, self._state_path)
        except OSError:
            logger.exception("could not save the watch folder state")

    def _trim(self) -> None:
        while len(self._seen) > MAX_SEEN_FILES:
            self._seen.pop(next(iter(self._seen)))

    def scan(self) -> list[tuple[str, str]]:
        events: list[tuple[str, str]] = []
        try:
            entries = list(self.folder.iterdir())
        except OSError:
            return events
        changed = False
        for path in entries:
            if path.suffix.lower() not in WATCH_SUFFIXES:
                continue
            try:
                if not path.is_file():
                    continue
                stat = path.stat()
            except OSError:
                continue
            signature = f"{int(stat.st_mtime)}:{stat.st_size}"
            if self._seen.get(path.name) == signature:
                continue
            self._seen[path.name] = signature
            changed = True
            url = read_link_file(path)
            if url:
                logger.info("found a link in %s", path.name)
                events.append((url, "watch folder"))
        if changed:
            self._trim()
            self._save()
        return events

    def run(self) -> None:
        while not self._stop.wait(self.interval):
            for url, source in self.scan():
                try:
                    self._on_link(url, source)
                except Exception:
                    logger.exception("watch folder link callback failed")

    def stop(self) -> None:
        self._stop.set()


class DetectionRunner:
    """Starts and stops watchers to match the current configuration."""

    def __init__(self, config) -> None:
        self._on_link: Callable[[str, str], None] | None = None
        self._watchers: list = []
        self._wanted: tuple[bool, str] | None = None
        self._state_path = config.state_path / "watch.json"

    def configure(self, on_link: Callable[[str, str], None]) -> None:
        self._on_link = on_link

    def apply(self, config) -> None:
        folder = os.path.expanduser(config.watch_folder.strip()) if config.watch_folder else ""
        wanted = (bool(config.clipboard_monitor), folder)
        if wanted == self._wanted:
            return
        self.stop()
        if self._on_link is None:
            return
        clipboard, folder = wanted
        if clipboard and clipboard_available():
            watcher = ClipboardWatcher(self._on_link)
            watcher.start()
            self._watchers.append(watcher)
            logger.info("watching the clipboard for links")
        elif clipboard:
            logger.warning("clipboard link detection is not available on this system")
        if folder:
            path = Path(folder)
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                logger.warning("could not create the watch folder %s: %s", path, exc)
            else:
                if path.is_dir():
                    watcher = FolderWatcher(path, self._on_link, self._state_path)
                    watcher.start()
                    self._watchers.append(watcher)
                    logger.info("watching %s for link files", path)
                else:
                    logger.warning("%s is not a folder", path)
        self._wanted = wanted

    def stop(self) -> None:
        for watcher in self._watchers:
            watcher.stop()
        for watcher in self._watchers:
            watcher.join(timeout=2.0)
        self._watchers = []
