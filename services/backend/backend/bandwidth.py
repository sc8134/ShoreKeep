from __future__ import annotations

"""Token-bucket bandwidth throttle for download streams.

Works with both MY-DL and yt-dlp transfer paths. The throttle is applied
per-stream: a global limit is shared across all active downloads while a
per-job limit caps that specific transfer.

A limit of 0 or None means unlimited.
"""

import threading
import time
from typing import Callable

ProgressCb = Callable[[int, "int | None", float, "float | None"], None]


class TokenBucket:
    """Leaky-token-bucket rate limiter.

    capacity  — burst allowance in bytes (defaults to 2× the per-second rate)
    rate_bps  — sustained rate in bytes/second; 0 = unlimited
    """

    def __init__(self, rate_bps: float, capacity: float | None = None) -> None:
        self.rate_bps = rate_bps
        self.capacity = capacity if capacity is not None else max(rate_bps * 2, 65536)
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def consume(self, nbytes: int) -> None:
        """Block until nbytes can be consumed from the bucket."""
        if self.rate_bps <= 0:
            return
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate_bps)
            self._last = now

            if self._tokens >= nbytes:
                self._tokens -= nbytes
                return
            # Need to wait for enough tokens.
            deficit = nbytes - self._tokens
            wait = deficit / self.rate_bps
            self._tokens = 0

        time.sleep(wait)

    def update_rate(self, rate_bps: float) -> None:
        with self._lock:
            self.rate_bps = rate_bps
            if rate_bps > 0:
                self.capacity = rate_bps * 2


# ---------------------------------------------------------------------------
# Global bandwidth state (in-process, per-worker)
# ---------------------------------------------------------------------------

_global_lock = threading.Lock()
_global_limit_bps: float = 0.0   # 0 = unlimited
_global_bucket: TokenBucket | None = None


def set_global_limit(kbps: float) -> None:
    """Set the global download speed limit in KB/s. 0 = unlimited."""
    global _global_limit_bps, _global_bucket
    bps = kbps * 1024.0
    with _global_lock:
        _global_limit_bps = bps
        if bps > 0:
            if _global_bucket is None:
                _global_bucket = TokenBucket(bps)
            else:
                _global_bucket.update_rate(bps)
        else:
            _global_bucket = None


def get_global_limit_kbps() -> float:
    return _global_limit_bps / 1024.0


def _effective_bps(job_bps: float) -> float:
    """Effective rate = min(global, per-job), 0 on either side means that side is unlimited."""
    with _global_lock:
        g = _global_limit_bps
    if g <= 0 and job_bps <= 0:
        return 0.0
    if g <= 0:
        return job_bps
    if job_bps <= 0:
        return g
    return min(g, job_bps)


# ---------------------------------------------------------------------------
# Throttled progress callback wrapper
# ---------------------------------------------------------------------------

def throttled_progress(
    on_progress: ProgressCb,
    job_limit_kbps: float = 0.0,
) -> ProgressCb:
    """Wrap a progress callback to throttle download speed.

    Throttling works by tracking bytes since the last call and sleeping
    via the token bucket when the rate is exceeded. The speed reported to
    the UI reflects the actual (throttled) speed, not the raw transfer speed.
    """
    effective_bps = _effective_bps(job_limit_kbps * 1024.0)
    if effective_bps <= 0:
        # No throttling — return original callback unchanged.
        return on_progress

    bucket = TokenBucket(effective_bps)
    _state = {"last_bytes": 0}

    def _callback(downloaded: int, total: "int | None", speed: float, eta: "float | None") -> None:
        delta = max(0, downloaded - _state["last_bytes"])
        _state["last_bytes"] = downloaded
        if delta > 0:
            bucket.consume(delta)
        # Recalculate speed after throttle so UI reflects real throughput.
        capped_speed = min(speed, effective_bps) if speed > 0 else speed
        on_progress(downloaded, total, capped_speed, eta)

    return _callback
