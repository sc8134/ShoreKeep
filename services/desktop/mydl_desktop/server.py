from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from backend.security import UnsafeURLError, assert_public_http_url

from . import __version__, metering
from .config import AGENT_HOST, AGENT_PORT, AgentConfig
from .cookies import probe_browsers
from .jobs import JobBusy, JobManager

logger = logging.getLogger(__name__)

ALLOWED_HOSTS = {f"{AGENT_HOST}:{AGENT_PORT}", f"localhost:{AGENT_PORT}"}
PRESETS = {"best", "1080p", "720p", "480p", "audio"}
JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class RunRequest(BaseModel):
    url: str = Field(min_length=10, max_length=2048)
    preset: str = "best"
    subtitles: bool = False
    job_id: str | None = None
    cookie_browser: str | None = None


class PairRegistry:
    """Resolves pair tokens from the web app into a paired user.

    The first time a pair token is seen it is exchanged (once) on the API server
    for a device token. The mapping is cached in memory for this agent process.
    """

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self._lock = threading.Lock()
        self._claim_lock = threading.Lock()
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def resolve(self, key: str | None) -> dict[str, Any] | None:
        if not key or len(key) < 20 or len(key) > 128:
            return None
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] > now:
                return cached[1]

        # A browser poll and a shorekeep:// handler can race to claim the same one-time
        # token; serialize so the second caller sees the cached result instead.
        with self._claim_lock:
            with self._lock:
                cached = self._cache.get(key)
                if cached and cached[0] > now:
                    return cached[1]
            identity = self._claim(key)
            if identity is None:
                return None
            with self._lock:
                cutoff = now - 60
                for stale in [k for k, entry in self._cache.items() if entry[0] < cutoff]:
                    self._cache.pop(stale, None)
                self._cache[key] = (now + 3600, identity)
            return identity

    def _claim(self, pair_token: str) -> dict[str, Any] | None:
        payload = json.dumps(
            {"pair_token": pair_token, "label": self.config.device_label}
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.config.server_base}/v1/agent/pair/claim",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                data = json.load(response)
        except (urllib.error.URLError, ValueError, OSError) as exc:
            logger.warning("pair claim failed: %s", exc)
            return None

        token = data.get("device_token")
        if token and token != self.config.device_token:
            self.config.device_token = token
            self.config.save()
        user = data.get("user") or {}
        return {
            "user": {"id": user.get("id", ""), "email": user.get("email", ""), "plan": user.get("plan", "free")},
            "limits": data.get("limits") or {},
            "device_token": token,
        }


def create_app(config: AgentConfig, manager: JobManager) -> FastAPI:
    registry = PairRegistry(config)

    app = FastAPI(title="Shorekeep Desktop Agent", version=__version__, docs_url=None, redoc_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.allowed_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Agent-Key"],
        allow_credentials=False,
        max_age=600,
    )

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.headers.get("host") or "").lower()
        if host not in ALLOWED_HOSTS:
            return JSONResponse({"detail": "Forbidden host"}, status_code=403)
        manager.touch()
        return await call_next(request)

    def _identity_or_401(key: str | None) -> dict[str, Any]:
        identity = registry.resolve(key)
        if identity is None:
            raise HTTPException(
                status_code=401,
                detail=(
                    "This web page is not paired with the desktop app. "
                    "Click “Download with app” again."
                ),
            )
        return identity

    def require_pair(x_agent_key: str = Header(default="")) -> dict[str, Any]:
        return _identity_or_401(x_agent_key)

    def require_pair_query(key: str = Query(default="")) -> dict[str, Any]:
        return _identity_or_401(key)

    def _visible_job(job_id: str, identity: dict[str, Any]):
        job = manager.get(job_id)
        if job is None or job.user_id != identity["user"]["id"]:
            raise HTTPException(status_code=404, detail="Job not found")
        return job

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "app": "shorekeep-desktop",
            "version": __version__,
            "paired": bool(config.device_token),
            "download_dir": str(config.download_path),
            "browsers": probe_browsers(),
        }

    @app.get("/v1/me")
    def me(identity: dict[str, Any] = Depends(require_pair)) -> dict[str, Any]:
        return {
            "user": identity["user"],
            "limits": identity["limits"],
            "download_dir": str(config.download_path),
            "browsers": probe_browsers(),
        }

    @app.post("/v1/run", status_code=201)
    def run(
        payload: RunRequest,
        identity: dict[str, Any] = Depends(require_pair),
    ) -> dict[str, Any]:
        url = payload.url.strip()
        if not url.lower().startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="Enter a valid http(s) URL")
        preset = payload.preset or "best"
        if preset not in PRESETS:
            raise HTTPException(status_code=400, detail=f"preset must be one of {sorted(PRESETS)}")

        allowed = identity["limits"].get("presets") or sorted(PRESETS)
        if preset not in allowed:
            raise HTTPException(status_code=403, detail=f"Your plan allows: {', '.join(allowed)}")

        cookie_mode = (payload.cookie_browser or config.cookie_browser).lower()
        if cookie_mode not in {"auto", "firefox", "chrome", "edge", "brave", "chromium", "off"}:
            cookie_mode = config.cookie_browser

        try:
            assert_public_http_url(url)
        except UnsafeURLError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        job_id = payload.job_id or uuid.uuid4().hex
        if not JOB_ID_RE.match(job_id):
            raise HTTPException(status_code=400, detail="Invalid job id")
        if manager.get(job_id) is not None:
            raise HTTPException(status_code=409, detail="That job id is already in use")

        try:
            manager.ensure_capacity()
        except JobBusy as exc:
            raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "15"}) from exc

        try:
            metering.reserve(config, job_id=job_id, url=url, preset=preset)
        except metering.QuotaExceeded as exc:
            raise HTTPException(
                status_code=429,
                detail=str(exc),
                headers={"Retry-After": str(exc.retry_after)},
            ) from exc
        except metering.PairingLost as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except metering.Rejected as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except metering.Unavailable as exc:
            logger.warning("usage server unreachable (%s); recording the download locally", exc)
            metering.queue_reserve(config, job_id=job_id, url=url, preset=preset)

        try:
            job = manager.submit(
                url=url,
                preset=preset,
                job_id=job_id,
                user=identity["user"],
                cookie_browser=cookie_mode,
                subtitles=payload.subtitles,
            )
        except JobBusy as exc:
            metering.complete(
                config,
                job_id=job_id,
                status="failed",
                preset=preset,
                url=url,
                size_bytes=0,
                error="Not started: app busy",
                started_at=datetime.now(timezone.utc),
            )
            raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "15"}) from exc

        return job.to_dict()

    @app.get("/v1/jobs")
    def list_jobs(
        limit: int = 20,
        identity: dict[str, Any] = Depends(require_pair),
    ) -> list[dict[str, Any]]:
        return manager.list_for_user(identity["user"]["id"], max(1, min(limit, 50)))

    @app.get("/v1/jobs/{job_id}")
    def get_job(
        job_id: str,
        identity: dict[str, Any] = Depends(require_pair),
        x_agent_key: str = Header(default=""),
    ) -> dict[str, Any]:
        job = _visible_job(job_id, identity)
        return job.to_dict(x_agent_key)

    @app.get("/v1/jobs/{job_id}/file")
    def download_file(
        job_id: str,
        key: str = Query(default=""),
        identity: dict[str, Any] = Depends(require_pair_query),
    ):
        job = _visible_job(job_id, identity)
        if job.status != "done" or not job.output_path:
            raise HTTPException(status_code=404, detail="File is not ready")
        path = Path(job.output_path)
        if not path.exists():
            raise HTTPException(status_code=410, detail="File was moved or deleted")
        return FileResponse(path, media_type=job.mime or "application/octet-stream", filename=job.output_name)

    @app.get("/v1/jobs/{job_id}/subtitles")
    def download_subtitles(
        job_id: str,
        key: str = Query(default=""),
        identity: dict[str, Any] = Depends(require_pair_query),
    ):
        job = _visible_job(job_id, identity)
        if job.status != "done" or not job.subtitle_path:
            raise HTTPException(status_code=404, detail="Subtitles are not available")
        path = Path(job.subtitle_path)
        if not path.exists():
            raise HTTPException(status_code=410, detail="Subtitles were moved or deleted")
        return FileResponse(path, media_type="text/vtt", filename=job.subtitle_name or path.name)

    return app


def start_idle_watch(manager: JobManager, minutes: int) -> None:
    if minutes <= 0:
        return

    def watch() -> None:
        while True:
            time.sleep(60)
            idle = time.monotonic() - manager.last_activity
            if manager.active_count() == 0 and idle > minutes * 60:
                logger.info("desktop agent idle for %.0f minutes; exiting", idle / 60)
                os._exit(0)

    threading.Thread(target=watch, name="idle-watch", daemon=True).start()
