import pytest

from backend.config import settings
from backend.engine.pipeline import Reporter, friendly_error
from backend.engine.types import JobTimeout, SizeLimitExceeded


def test_reporter_rejects_oversized_total(monkeypatch):
    monkeypatch.setattr(settings, "max_filesize_mb", 1)
    reporter = Reporter("test-job")
    with pytest.raises(SizeLimitExceeded):
        reporter._enforce_limits(downloaded=0, total=5 * 1024 * 1024)


def test_reporter_rejects_oversized_progress(monkeypatch):
    monkeypatch.setattr(settings, "max_filesize_mb", 1)
    reporter = Reporter("test-job")
    with pytest.raises(SizeLimitExceeded):
        reporter._enforce_limits(downloaded=2 * 1024 * 1024, total=None)


def test_reporter_rejects_timed_out_job(monkeypatch):
    monkeypatch.setattr(settings, "job_timeout_seconds", 5)
    reporter = Reporter("test-job")
    reporter._started -= 30
    with pytest.raises(JobTimeout):
        reporter._enforce_limits(downloaded=0, total=None)


def test_friendly_error_for_limits():
    assert "limit" in friendly_error(SizeLimitExceeded("File is larger than the 10 MB limit"))
    assert "time limit" in friendly_error(JobTimeout("Job exceeded the 60s time limit"))
