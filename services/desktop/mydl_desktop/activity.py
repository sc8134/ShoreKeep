from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_ENTRIES = 200


class ActivityStore:
    """Persistent record of finished desktop downloads for history and notifications."""

    def __init__(self, path: Path, limit: int = MAX_ENTRIES) -> None:
        self.path = path
        self.limit = limit
        self._lock = threading.Lock()
        self._entries: list[dict[str, Any]] = self._load()

    def _load(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        return [entry for entry in data if isinstance(entry, dict)][-self.limit :]

    def record(self, entry: dict[str, Any]) -> dict[str, Any]:
        event = dict(entry)
        event.setdefault("finished_at", datetime.now(timezone.utc).isoformat())
        with self._lock:
            # A job can only finish once, but retries through the same id are possible.
            self._entries = [item for item in self._entries if item.get("id") != event.get("id")]
            self._entries.append(event)
            self._entries = self._entries[-self.limit :]
            self._write()
        return event

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            temporary.write_text(json.dumps(self._entries, indent=1), encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError:
            logger.exception("could not save download activity to %s", self.path)

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            entries = list(self._entries)
        entries.reverse()
        return entries[: max(1, limit)]

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._entries[-1]) if self._entries else None

    def find(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            for entry in reversed(self._entries):
                if entry.get("id") == job_id:
                    return dict(entry)
        return None
