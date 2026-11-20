import json
import zipfile

import pytest
from sqlmodel import Session

from backend.config import settings
from backend.db import engine
from backend.engine import pipeline
from backend.engine.types import MediaPlan
from backend.models import Job


def make_plan(**overrides) -> MediaPlan:
    plan = MediaPlan(
        title="Clip",
        preset="720p",
        mode="progressive",
        ext="mp4",
        webpage_url="https://example.com/watch?v=1",
        engine="mydl",
        direct=True,
    )
    for key, value in overrides.items():
        setattr(plan, key, value)
    return plan


def isolate_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(pipeline, "assert_public_http_url", lambda url: None)
    (tmp_path / "media").mkdir(parents=True, exist_ok=True)


def fake_download(plan, work, reporter, cookiefile=None, start=8, end=90):
    dest = work / f"media.{plan.ext}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"video-bytes")
    reporter.download_progress(start, end)(len(b"video-bytes"), len(b"video-bytes"), 0, 0)
    return dest


def add_job(**overrides) -> Job:
    values = {
        "id": "phase4-job",
        "url": "https://example.com/watch?v=1",
        "preset": "720p",
    }
    values.update(overrides)
    with Session(engine) as session:
        job = Job(**values)
        session.add(job)
        session.commit()
    return job


def test_playlist_job_builds_one_zip(tmp_path, monkeypatch):
    isolate_dirs(tmp_path, monkeypatch)
    monkeypatch.setattr(pipeline, "_download_once", fake_download)
    monkeypatch.setattr(
        pipeline,
        "extract_info",
        lambda url, cookiefile=None, playlist=False, limit=None: {
            "_type": "playlist",
            "title": "Mix",
            "entries": [
                {"_type": "url", "title": "One", "url": "https://example.com/one"},
                {"_type": "url", "title": "Two", "url": "https://example.com/two"},
            ],
        },
    )
    monkeypatch.setattr(pipeline, "build_plan", lambda info, preset, max_bytes=0: make_plan())
    add_job(id="playlist-job", url="https://example.com/playlist")

    pipeline.run_job("playlist-job")

    with Session(engine) as session:
        job = session.get(Job, "playlist-job")
        assert job is not None
        assert job.status == "done", job.error
        assert job.playlist_count == 2
        assert job.playlist_index == 2
        assert job.output_name == "Mix.zip"
        with zipfile.ZipFile(job.output_path) as archive:
            assert archive.namelist() == ["01 - Clip.mp4", "02 - Clip.mp4"]


def test_subtitles_and_chapters_are_stored(tmp_path, monkeypatch):
    isolate_dirs(tmp_path, monkeypatch)
    monkeypatch.setattr(pipeline, "_download_once", fake_download)
    monkeypatch.setattr(
        pipeline,
        "extract_info",
        lambda url, cookiefile=None, playlist=False, limit=None: {
            "_type": "video",
            "title": "Clip",
            "webpage_url": url,
            "chapters": [{"start_time": 0, "title": "Intro"}],
        },
    )
    monkeypatch.setattr(pipeline, "build_plan", lambda info, preset, max_bytes=0: make_plan())

    def fake_subtitle(info, work):
        path = work / "subtitles.en.vtt"
        path.write_text("WEBVTT\n", encoding="utf-8")
        return path, "en"

    monkeypatch.setattr(pipeline, "fetch_subtitle", fake_subtitle)
    add_job(id="subtitle-job", want_subtitles=True)

    pipeline.run_job("subtitle-job")

    with Session(engine) as session:
        job = session.get(Job, "subtitle-job")
        assert job is not None
        assert job.status == "done", job.error
        assert job.subtitle_name == "Clip.en.vtt"
        assert job.subtitle_lang == "en"
        assert job.chapters == json.dumps([{"start": 0.0, "title": "Intro"}])

    from backend.api.jobs import _to_read

    with Session(engine) as session:
        payload = _to_read(session.get(Job, "subtitle-job"))
    assert payload.subtitle_url == "/v1/jobs/subtitle-job/subtitles"
    assert payload.chapters == [{"start": 0.0, "title": "Intro"}]
    assert payload.retryable is False


def test_failure_records_error_code(tmp_path, monkeypatch):
    isolate_dirs(tmp_path, monkeypatch)
    monkeypatch.setattr(
        pipeline,
        "extract_info",
        lambda url, cookiefile=None, playlist=False, limit=None: {
            "_type": "video",
            "title": "Clip",
            "formats": [],
        },
    )
    monkeypatch.setattr(
        pipeline,
        "build_plan",
        lambda info, preset, max_bytes=0: make_plan(),
    )

    def fail(plan, work, reporter, cookiefile=None, start=8, end=90):
        raise RuntimeError("ERROR: Sign in to confirm you're not a bot")

    monkeypatch.setattr(pipeline, "_download_once", fail)
    add_job(id="failing-job")

    pipeline.run_job("failing-job")

    from backend.api.jobs import _to_read

    with Session(engine) as session:
        job = session.get(Job, "failing-job")
        assert job is not None
        assert job.status == "failed"
        assert job.error_code == "bot_check"
        payload = _to_read(job)
    assert payload.retryable is True
    assert "bot check" in payload.error.lower()
