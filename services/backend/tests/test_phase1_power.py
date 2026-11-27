"""Phase 1: bandwidth limiting, job pause/resume/reorder, auto-retry."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from sqlmodel import Session

from backend.bandwidth import TokenBucket, get_global_limit_kbps, set_global_limit, throttled_progress
from backend.db import engine
from backend.models import Job, utcnow


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _register(client, email="p1@example.com", password="password123"):
    r = client.post("/v1/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return r.cookies.get("mp_session")


def _make_job(job_id: str, status: str = "queued", **kwargs) -> Job:
    with Session(engine) as s:
        job = Job(
            id=job_id,
            url="https://example.com/video",
            preset="best",
            status=status,
            stage=status,
            progress=0.0,
            downloaded_bytes=0,
            speed_bps=0.0,
            **kwargs,
        )
        s.add(job)
        s.commit()
    return job


def _get_job(job_id: str) -> Job:
    with Session(engine) as s:
        return s.get(Job, job_id)


# ---------------------------------------------------------------------------
# Token bucket unit tests
# ---------------------------------------------------------------------------

def test_token_bucket_unlimited_no_sleep(client):
    bucket = TokenBucket(rate_bps=0)
    t0 = time.monotonic()
    bucket.consume(1024 * 1024)  # 1 MB — instant when unlimited
    assert time.monotonic() - t0 < 0.1


def test_token_bucket_rate_limits():
    # 128 KB/s bucket; burst capacity defaults to 2× = 256 KB
    bucket = TokenBucket(rate_bps=128 * 1024, capacity=128 * 1024)
    t0 = time.monotonic()
    # First 128 KB fits in capacity — no sleep
    bucket.consume(128 * 1024)
    # Second 128 KB requires waiting ~1s
    bucket.consume(128 * 1024)
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.9, f"Expected throttle, got {elapsed:.2f}s"


def test_throttled_progress_unlimited_passthrough(client):
    calls = []
    cb = lambda d, t, s, e: calls.append((d, t, s, e))
    wrapped = throttled_progress(cb, job_limit_kbps=0.0)
    assert wrapped is cb  # no-op wrapper when unlimited


def test_throttled_progress_caps_reported_speed(client):
    reported = []
    cb = lambda d, t, s, e: reported.append(s)
    # 512 KB/s limit
    wrapped = throttled_progress(cb, job_limit_kbps=512.0)
    # Simulate a fast download reporting 2 MB/s
    wrapped(1024, None, 2 * 1024 * 1024, None)
    assert reported, "callback was never called"
    assert reported[-1] <= 512 * 1024 + 1  # capped to limit


# ---------------------------------------------------------------------------
# Global bandwidth API
# ---------------------------------------------------------------------------

def test_get_bandwidth_requires_auth(client):
    r = client.get("/v1/bandwidth")
    assert r.status_code == 401


def test_set_and_get_global_limit(client):
    cookie = _register(client)
    # Set to 1024 KB/s
    r = client.post(
        "/v1/bandwidth",
        json={"limit_kbps": 1024},
        cookies={"mp_session": cookie},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["limit_kbps"] == 1024
    assert data["unlimited"] is False
    assert data["limit_mbps"] == 1.0

    # Read back
    r2 = client.get("/v1/bandwidth", cookies={"mp_session": cookie})
    assert r2.json()["limit_kbps"] == 1024


def test_set_unlimited(client):
    cookie = _register(client, "bw2@example.com")
    client.post("/v1/bandwidth", json={"limit_kbps": 500}, cookies={"mp_session": cookie})
    r = client.post("/v1/bandwidth", json={"limit_kbps": 0}, cookies={"mp_session": cookie})
    assert r.json()["unlimited"] is True
    assert get_global_limit_kbps() == 0.0


def test_negative_limit_rejected(client):
    cookie = _register(client, "bw3@example.com")
    r = client.post("/v1/bandwidth", json={"limit_kbps": -1}, cookies={"mp_session": cookie})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Per-job bandwidth on create
# ---------------------------------------------------------------------------

def test_job_create_stores_bandwidth_limit(client):
    cookie = _register(client, "bwjob@example.com")
    r = client.post(
        "/v1/jobs",
        json={"url": "https://example.com/v", "preset": "best", "bandwidth_limit_kbps": 256.0},
        cookies={"mp_session": cookie},
        headers={"origin": "http://127.0.0.1:8000"},
    )
    assert r.status_code == 201
    assert r.json()["bandwidth_limit_kbps"] == 256.0


def test_job_create_stores_max_retries(client):
    cookie = _register(client, "retry_cr@example.com")
    r = client.post(
        "/v1/jobs",
        json={"url": "https://example.com/v", "preset": "best", "max_retries": 3},
        cookies={"mp_session": cookie},
        headers={"origin": "http://127.0.0.1:8000"},
    )
    assert r.status_code == 201
    assert r.json()["max_retries"] == 3


# ---------------------------------------------------------------------------
# PATCH /v1/jobs/{id} — pause / resume / reorder / bandwidth
# ---------------------------------------------------------------------------

def test_pause_queued_job(client):
    _make_job("pause-job-1", status="queued")
    r = client.patch("/v1/jobs/pause-job-1", json={"action": "pause"})
    assert r.status_code == 200
    assert r.json()["status"] == "paused"
    assert _get_job("pause-job-1").status == "paused"


def test_resume_paused_job(client):
    _make_job("resume-job-1", status="paused")
    r = client.patch("/v1/jobs/resume-job-1", json={"action": "resume"})
    assert r.status_code == 200
    assert r.json()["status"] == "queued"


def test_cannot_pause_running_job(client):
    _make_job("running-job-1", status="downloading")
    r = client.patch("/v1/jobs/running-job-1", json={"action": "pause"})
    assert r.status_code == 400


def test_cannot_resume_non_paused_job(client):
    _make_job("queued-job-1", status="queued")
    r = client.patch("/v1/jobs/queued-job-1", json={"action": "resume"})
    assert r.status_code == 400


def test_move_top_bumps_priority(client):
    _make_job("move-top-1", status="queued", priority=0)
    r = client.patch("/v1/jobs/move-top-1", json={"action": "move_top"})
    assert r.status_code == 200
    job = _get_job("move-top-1")
    assert job.priority >= 1


def test_move_bottom_resets_priority(client):
    _make_job("move-bot-1", status="queued", priority=1)
    r = client.patch("/v1/jobs/move-bot-1", json={"action": "move_bottom"})
    assert r.status_code == 200
    assert _get_job("move-bot-1").priority == 0


def test_patch_bandwidth_limit(client):
    _make_job("bw-patch-1", status="queued")
    r = client.patch("/v1/jobs/bw-patch-1", json={"bandwidth_limit_kbps": 512.0})
    assert r.status_code == 200
    assert r.json()["bandwidth_limit_kbps"] == 512.0
    assert _get_job("bw-patch-1").bandwidth_limit_kbps == 512.0


def test_patch_unknown_action_returns_400(client):
    _make_job("unknown-action-1", status="queued")
    r = client.patch("/v1/jobs/unknown-action-1", json={"action": "fly"})
    assert r.status_code == 400


def test_patch_nonexistent_job_returns_404(client):
    r = client.patch("/v1/jobs/doesnotexist", json={"action": "pause"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Auto-retry
# ---------------------------------------------------------------------------

def test_auto_retry_re_queues_retryable_job(client):
    from backend.engine.worker import _auto_retry
    _make_job(
        "retry-job-1",
        status="failed",
        error_code="network",
        max_retries=3,
        retry_count=0,
    )
    _auto_retry("retry-job-1")
    job = _get_job("retry-job-1")
    assert job.status == "queued"
    assert job.retry_count == 1
    assert job.run_after is not None  # backoff applied


def test_auto_retry_stops_at_max(client):
    from backend.engine.worker import _auto_retry
    _make_job(
        "retry-job-2",
        status="failed",
        error_code="network",
        max_retries=2,
        retry_count=2,
    )
    _auto_retry("retry-job-2")
    # Should stay failed — exhausted retries
    assert _get_job("retry-job-2").status == "failed"


def test_auto_retry_does_not_retry_non_retryable(client):
    from backend.engine.worker import _auto_retry
    _make_job(
        "retry-job-3",
        status="failed",
        error_code="drm",
        max_retries=3,
        retry_count=0,
    )
    _auto_retry("retry-job-3")
    assert _get_job("retry-job-3").status == "failed"


def test_auto_retry_does_nothing_when_max_retries_zero(client):
    from backend.engine.worker import _auto_retry
    _make_job(
        "retry-job-4",
        status="failed",
        error_code="network",
        max_retries=0,
        retry_count=0,
    )
    _auto_retry("retry-job-4")
    assert _get_job("retry-job-4").status == "failed"


def test_auto_retry_exponential_backoff(client):
    from backend.engine.worker import _auto_retry
    # retry_count=0 → backoff = 15s
    _make_job("retry-backoff-1", status="failed", error_code="network", max_retries=3, retry_count=0)
    _auto_retry("retry-backoff-1")
    job = _get_job("retry-backoff-1")
    assert job.run_after is not None
    now = utcnow()
    delay = (job.run_after - now).total_seconds()
    assert 10 < delay <= 20, f"Expected ~15s backoff, got {delay:.1f}s"
