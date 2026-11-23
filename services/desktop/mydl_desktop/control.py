from __future__ import annotations

import json
import logging
import os
import socket
import threading
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
COMMAND = b"focus\n"
REPLY = b"ok\n"


class FocusChannel:
    """Small loopback listener so a second launch can raise the existing window."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None

    def start(self, focus: Callable[[], None]) -> bool:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if os.name == "nt":
                server.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((HOST, 0))
            server.listen(4)
            server.settimeout(1.0)
        except OSError:
            server.close()
            logger.exception("could not open the window focus channel")
            return False

        self._server = server
        try:
            self.path.write_text(
                json.dumps({"port": server.getsockname()[1], "pid": os.getpid()}),
                encoding="utf-8",
            )
            os.chmod(self.path, 0o600)
        except OSError:
            logger.exception("could not write %s", self.path)
            self.close()
            return False

        self._thread = threading.Thread(
            target=self._loop, args=(server, focus), name="focus-channel", daemon=True
        )
        self._thread.start()
        return True

    def _loop(self, server: socket.socket, focus: Callable[[], None]) -> None:
        while self._server is not None:
            try:
                connection, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                connection.settimeout(1.0)
                try:
                    data = connection.recv(64)
                except OSError:
                    continue
                if data.strip().lower() != b"focus":
                    continue
                try:
                    focus()
                except Exception:
                    logger.exception("focus callback failed")
                try:
                    connection.sendall(REPLY)
                except OSError:
                    pass

    def close(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
        try:
            self.path.unlink()
        except OSError:
            pass


def request_focus(path: Path, timeout: float = 1.0) -> bool:
    """Ask the running window to show itself. Returns False when no listener answers."""
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
        port = int(info["port"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    try:
        with socket.create_connection((HOST, port), timeout=timeout) as connection:
            connection.sendall(COMMAND)
            connection.settimeout(timeout)
            reply = connection.recv(16)
    except OSError:
        return False
    return reply.strip().lower() == REPLY.strip()
