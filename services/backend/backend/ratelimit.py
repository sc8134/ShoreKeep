from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class SlidingWindowLimiter:
    """In-process per-key sliding-window rate limiter.

    Accurate for a single API process (SQLite dev, single-worker deploys).
    On multi-worker Postgres deployments the DB-backed quota in db.quota_guard
    is the authoritative counter; this limiter acts as a per-worker burst guard
    that fires before the DB round-trip, not as the global enforcement layer.
    """

    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds).

        retry_after is 0 when the request is allowed and a positive integer
        (seconds to wait) when it is rejected.
        """
        if self.limit <= 0:
            return True, 0
        now = time.monotonic()
        cutoff = now - self.window
        with self._lock:
            hits = self._hits[key]
            # evict timestamps outside the current window
            while hits and hits[0] < cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                retry_after = max(1, int(hits[0] + self.window - now) + 1)
                return False, retry_after
            hits.append(now)
            # prevent unbounded memory growth when many unique keys accumulate
            if len(self._hits) > 50_000:
                self._evict(now)
            return True, 0

    def _evict(self, now: float) -> None:
        cutoff = now - self.window
        stale = [key for key, dq in self._hits.items() if not dq or dq[-1] < cutoff]
        for key in stale:
            self._hits.pop(key, None)
