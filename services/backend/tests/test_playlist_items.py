"""Phase 7: playlist per-item download links."""
from __future__ import annotations

import uuid

from sqlmodel import Session

from backend.db import engine
from backend.models import Job, utcnow


def _make_playlist_parent(job_id: str, count: int) -> None:
    with Session(engine) as s:
        parent = Job(
            id=job_id,
            url="https://example.com/playlist",
            preset="best",
            status="done",
            stage="done",
            progress=100.0,
            downloaded_bytes=1024,
            speed_bps=0.0,
            output_name="playlist.zip",
            output_path="/tmp/playlist.zip",
            output_size=1024,
            mime="application/zip",
            playlist_count=count,
            playlist_index=count,
            finished_at=utcnow(),
        )
        s.add(parent)
        for i in range(count):
            child = Job(
                id=uuid.uuid4().hex,
                url="",
                preset="best",
                status="done",
                stage="done",
                progress=100.0,
                downloaded_bytes=512,
                speed_bps=0.0,
                output_name=f"item{i}.mp4",
                output_path=f"/tmp/item{i}.mp4",
                output_size=512,
                mime="video/mp4",
                parent_job_id=job_id,
                item_index=i,
                finished_at=utcnow(),
            )
            s.add(child)
        s.commit()


def test_items_endpoint_returns_children(client):
    _make_playlist_parent("playlist-parent-1", 3)
    r = client.get("/v1/jobs/playlist-parent-1/items")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 3
    # Items are ordered by item_index
    assert items[0]["output_name"] == "item0.mp4"
    assert items[1]["output_name"] == "item1.mp4"
    assert items[2]["output_name"] == "item2.mp4"


def test_items_endpoint_on_non_playlist_returns_404(client):
    with Session(engine) as s:
        job = Job(
            id="single-job-1",
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

    r = client.get("/v1/jobs/single-job-1/items")
    assert r.status_code == 404


def test_items_endpoint_on_missing_job_returns_404(client):
    r = client.get("/v1/jobs/doesnotexist/items")
    assert r.status_code == 404


def test_items_url_in_job_response(client):
    _make_playlist_parent("playlist-parent-2", 2)
    r = client.get("/v1/jobs/playlist-parent-2")
    assert r.status_code == 200
    data = r.json()
    assert data["items_url"] == "/v1/jobs/playlist-parent-2/items"


def test_items_url_absent_for_single_job(client):
    with Session(engine) as s:
        job = Job(
            id="single-job-2",
            url="https://example.com/v",
            preset="best",
            status="done",
            stage="done",
            progress=100.0,
            downloaded_bytes=512,
            speed_bps=0.0,
            output_name="v.mp4",
            output_path="/tmp/v.mp4",
            output_size=512,
            mime="video/mp4",
            finished_at=utcnow(),
        )
        s.add(job)
        s.commit()

    r = client.get("/v1/jobs/single-job-2")
    assert r.status_code == 200
    assert r.json()["items_url"] is None
