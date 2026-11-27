"""Phase 5: priority queue and scheduling."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from backend.db import engine
from backend.engine.worker import claim_job
from backend.models import Job, utcnow


def _make_job(job_id: str, priority: int = 0, run_after: datetime | None = None) -> Job:
    with Session(engine) as s:
        job = Job(
            id=job_id,
            url="https://example.com/video",
            preset="best",
            status="queued",
            stage="queued",
            progress=0.0,
            downloaded_bytes=0,
            speed_bps=0.0,
            priority=priority,
            run_after=run_after,
        )
        s.add(job)
        s.commit()
    return job


def _get_job(job_id: str) -> Job:
    with Session(engine) as s:
        return s.get(Job, job_id)


# ---------------------------------------------------------------------------
# worker claim_job ordering
# ---------------------------------------------------------------------------

def test_high_priority_claimed_before_normal(client):
    _make_job("normal-job-1", priority=0)
    _make_job("high-job-1", priority=1)

    claimed = claim_job()
    assert claimed == "high-job-1"


def test_scheduled_job_not_claimed_before_run_after(client):
    future = utcnow() + timedelta(hours=2)
    _make_job("future-job-1", run_after=future)

    claimed = claim_job()
    assert claimed is None


def test_scheduled_job_claimed_after_run_after(client):
    past = utcnow() - timedelta(seconds=1)
    _make_job("past-job-1", run_after=past)

    claimed = claim_job()
    assert claimed == "past-job-1"


def test_normal_job_claimed_when_no_priority_set(client):
    _make_job("norm-only-1", priority=0)
    claimed = claim_job()
    assert claimed == "norm-only-1"


# ---------------------------------------------------------------------------
# API validation
# ---------------------------------------------------------------------------

def _register_pro(client, email):
    r = client.post("/v1/auth/register", json={"email": email, "password": "password123"})
    assert r.status_code == 201
    # Manually set plan to pro via DB
    with Session(engine) as s:
        from backend.models import User
        from sqlmodel import select as sel
        u = s.exec(sel(User).where(User.email == email)).first()
        u.plan = "pro"
        s.add(u)
        s.commit()
    return r.cookies.get("mp_session")


def _register_free(client, email):
    r = client.post("/v1/auth/register", json={"email": email, "password": "password123"})
    assert r.status_code == 201
    return r.cookies.get("mp_session")


def test_free_user_cannot_set_priority(client):
    cookie = _register_free(client, "free_prio@example.com")
    r = client.post(
        "/v1/jobs",
        json={"url": "https://example.com/video", "preset": "best", "priority": 1},
        cookies={"mp_session": cookie},
        headers={"origin": "http://127.0.0.1:8000"},
    )
    assert r.status_code == 403


def test_pro_user_can_set_priority(client):
    cookie = _register_pro(client, "pro_prio@example.com")
    r = client.post(
        "/v1/jobs",
        json={"url": "https://example.com/video", "preset": "best", "priority": 1},
        cookies={"mp_session": cookie},
        headers={"origin": "http://127.0.0.1:8000"},
    )
    # 201 means accepted — priority field stored
    assert r.status_code == 201


def test_run_after_in_past_rejected(client):
    cookie = _register_free(client, "sched_past@example.com")
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    r = client.post(
        "/v1/jobs",
        json={"url": "https://example.com/video", "preset": "best", "run_after": past},
        cookies={"mp_session": cookie},
        headers={"origin": "http://127.0.0.1:8000"},
    )
    assert r.status_code == 400


def test_run_after_too_far_rejected(client):
    cookie = _register_free(client, "sched_far@example.com")
    far = (datetime.now(timezone.utc) + timedelta(days=8)).isoformat()
    r = client.post(
        "/v1/jobs",
        json={"url": "https://example.com/video", "preset": "best", "run_after": far},
        cookies={"mp_session": cookie},
        headers={"origin": "http://127.0.0.1:8000"},
    )
    assert r.status_code == 400


def test_run_after_valid_accepted(client):
    cookie = _register_free(client, "sched_ok@example.com")
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    r = client.post(
        "/v1/jobs",
        json={"url": "https://example.com/video", "preset": "best", "run_after": future},
        cookies={"mp_session": cookie},
        headers={"origin": "http://127.0.0.1:8000"},
    )
    assert r.status_code == 201
