"""Phase 3: SSE works in DB-poll mode (no Redis in test env)."""
from __future__ import annotations

from backend.pubsub import IS_REDIS


def test_redis_disabled_in_test_env():
    # Test environment has no REDIS_URL so IS_REDIS must be False.
    assert IS_REDIS is False


def test_sse_endpoint_accessible_without_redis(client):
    # Register, create a job row in done state, and confirm SSE yields data.
    r = client.post("/v1/auth/register", json={"email": "sse@example.com", "password": "password123"})
    assert r.status_code == 201
    session_cookie = r.cookies.get("mp_session")

    # Directly create a job in done state via the DB so SSE exits cleanly.
    from backend.db import engine
    from backend.models import Job, utcnow
    from sqlmodel import Session

    job_id = "ssetestjob01"
    with Session(engine) as s:
        job = Job(
            id=job_id,
            url="https://example.com/video",
            preset="best",
            status="done",
            stage="done",
            progress=100.0,
            downloaded_bytes=1024,
            speed_bps=0.0,
            output_name="video.mp4",
            output_path="/tmp/video.mp4",
            output_size=1024,
            mime="video/mp4",
            finished_at=utcnow(),
        )
        s.add(job)
        s.commit()

    # SSE on a done job should emit one event and close.
    with client.stream("GET", f"/v1/jobs/{job_id}/events") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = []
        for line in resp.iter_lines():
            if line.startswith("data: "):
                import json
                events.append(json.loads(line[6:]))
        assert len(events) >= 1
        assert events[-1]["status"] == "done"


def test_sse_job_not_found_returns_404(client):
    r = client.get("/v1/jobs/doesnotexist/events")
    assert r.status_code == 404
