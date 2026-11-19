from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import AgentConfig, config_dir

logger = logging.getLogger(__name__)

_lock = threading.RLock()
MAX_EVENTS = 500
TIMEOUT = 6.0


class MeteringError(RuntimeError):
    pass


class QuotaExceeded(MeteringError):
    def __init__(self, message: str, retry_after: int = 60) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class PairingLost(MeteringError):
    pass


class Rejected(MeteringError):
    pass


class Unavailable(MeteringError):
    pass


def _outbox_path(config: AgentConfig) -> Path:
    return config.state_path / "outbox.jsonl"


def _request(config: AgentConfig, method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{config.server_base.rstrip('/')}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.device_token or ''}",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            detail = json.loads(body).get("detail") or body
        except ValueError:
            detail = body
        detail = str(detail).strip() or f"HTTP {exc.code}"
        if exc.code == 429:
            retry_after = int(exc.headers.get("Retry-After") or 60)
            raise QuotaExceeded(detail, retry_after) from exc
        if exc.code == 401:
            raise PairingLost(
                "The desktop app is no longer paired with this account. "
                "Click “Download with app” in the web app to pair it again."
            ) from exc
        if exc.code in {400, 403, 404, 409, 422}:
            raise Rejected(detail) from exc
        raise Unavailable(f"the server returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise Unavailable(str(exc)) from exc


def reserve(config: AgentConfig, *, job_id: str, url: str, preset: str) -> dict[str, Any]:
    """Ask the server to count this download and check the plan quota."""
    if not config.device_token:
        raise PairingLost(
            "The desktop app is not paired with an account. Click “Download with app” again."
        )
    return _request(
        config,
        "POST",
        "/v1/agent/jobs",
        {"job_id": job_id, "url": url, "preset": preset},
    )


def complete(
    config: AgentConfig,
    *,
    job_id: str,
    status: str,
    preset: str,
    url: str,
    size_bytes: int,
    error: str | None,
    started_at: datetime,
) -> None:
    """Report a finished download. Never raises; queues the event when offline."""
    event = {
        "kind": "complete",
        "job_id": job_id,
        "status": status,
        "preset": preset,
        "url": url,
        "size_bytes": max(0, int(size_bytes or 0)),
        "error": (error or "")[:500] or None,
        "started_at": _iso(started_at),
    }
    try:
        _request(config, "PUT", f"/v1/agent/jobs/{job_id}", {k: v for k, v in event.items() if k != "kind"})
    except Unavailable as exc:
        logger.warning("could not report usage for %s (%s); queued for retry", job_id, exc)
        _append(config, event)
        return
    except QuotaExceeded:
        logger.warning("usage report for %s was rate-limited; dropping", job_id)
        return
    except (PairingLost, Rejected) as exc:
        logger.warning("usage report for %s rejected: %s", job_id, exc)
        return
    except Exception:
        logger.exception("unexpected error while reporting usage for %s; queued for retry", job_id)
        _append(config, event)
        return
    try:
        flush(config)
    except Exception:
        logger.exception("usage outbox flush failed")


def queue_reserve(config: AgentConfig, *, job_id: str, url: str, preset: str) -> None:
    _append(
        config,
        {
            "kind": "reserve",
            "job_id": job_id,
            "url": url,
            "preset": preset,
            "started_at": _iso(datetime.now(timezone.utc)),
        }
    )


def flush(config: AgentConfig) -> int:
    """Replay queued usage events. Returns how many were delivered or dropped."""
    if not config.device_token:
        return 0
    with _lock:
        events = _read(config)
        if not events:
            return 0
        sent = 0
        remaining = events
        for index, event in enumerate(events):
            try:
                if event.get("kind") == "reserve":
                    payload = {k: v for k, v in event.items() if k != "kind"}
                    _request(config, "POST", "/v1/agent/jobs", payload)
                else:
                    payload = {k: v for k, v in event.items() if k != "kind"}
                    _request(config, "PUT", f"/v1/agent/jobs/{event['job_id']}", payload)
                sent += 1
            except QuotaExceeded:
                logger.warning("dropping replay for %s: quota was exceeded", event.get("job_id"))
                sent += 1
            except (PairingLost, Rejected) as exc:
                logger.warning("dropping replay for %s: %s", event.get("job_id"), exc)
                sent += 1
            except Unavailable:
                remaining = events[index:]
                break
        else:
            remaining = []
        _write(config, remaining)
        return sent


def start_flusher(config: AgentConfig, interval: float = 60.0) -> None:
    def loop() -> None:
        while True:
            time.sleep(interval)
            try:
                flush(config)
            except Exception:
                logger.exception("usage outbox flush failed")

    threading.Thread(target=loop, name="metering-flush", daemon=True).start()


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _append(config: AgentConfig, event: dict[str, Any]) -> None:
    with _lock:
        events = _read(config)
        events.append(event)
        _write(config, events[-MAX_EVENTS:])


def _read(config: AgentConfig) -> list[dict[str, Any]]:
    path = _outbox_path(config)
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("kind") in {"reserve", "complete"}:
            events.append(event)
    return events


def _write(config: AgentConfig, events: list[dict[str, Any]]) -> None:
    path = _outbox_path(config)
    try:
        if not events:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            "".join(json.dumps(event, separators=(",", ":")) + "\n" for event in events),
            encoding="utf-8",
        )
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except OSError:
        logger.exception("could not write usage outbox")
