from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import IO

from .config import config_dir


class SingleInstance:
    """Cross-platform advisory lock so only one tray or window runs per user."""

    def __init__(self, name: str) -> None:
        self.path = config_dir() / f"{name}.lock"
        self._handle: IO[bytes] | None = None

    def acquire(self) -> bool:
        if self._handle is not None:
            return True
        handle = open(self.path, "a+b")
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            handle.close()


def open_path(path: Path | str) -> None:
    target = str(path)
    if os.name == "nt":
        os.startfile(target)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])


def reveal_path(path: Path | str) -> None:
    """Show a file in the system file manager, selecting it when supported."""
    target = Path(path)
    if os.name == "nt":
        subprocess.Popen(["explorer", f"/select,{target}"])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target.parent if target.is_file() else target)])
