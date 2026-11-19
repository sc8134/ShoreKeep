from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class SlidingWindowLimiter:
    """In-process per-key request limiter (per API worker)."""

    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, int]:
        if self.limit <= 0:
            return True, 0
        now = time.monotonic()
        cutoff = now - self.window
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] < cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                retry_after = max(1, int(hits[0] + self.window - now) + 1)
                return False, retry_after
            hits.append(now)
            return True, 0
