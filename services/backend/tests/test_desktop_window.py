import io
import json

import pytest

pytest.importorskip("mydl_desktop")

from mydl_desktop import window


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _health(monkeypatch, payload):
    def fake_urlopen(request, timeout=None):
        return _FakeResponse(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(window.urllib.request, "urlopen", fake_urlopen)


def test_title_shows_progress_for_one_job(monkeypatch):
    _health(
        monkeypatch,
        {"ok": True, "active_jobs": [{"title": "A calm shoreline", "progress": 42.6}]},
    )
    assert window._TitleWatcher(None)._title() == "Shorekeep · 43% · A calm shoreline"


def test_title_averages_progress_across_jobs(monkeypatch):
    _health(
        monkeypatch,
        {
            "ok": True,
            "active_jobs": [
                {"title": "First", "progress": 20},
                {"title": "Second", "progress": 80},
            ],
        },
    )
    assert window._TitleWatcher(None)._title() == "Shorekeep · 50% · 2 downloads"


def test_title_truncates_long_names(monkeypatch):
    name = "A very long video title that keeps going and going"
    _health(monkeypatch, {"ok": True, "active_jobs": [{"title": name, "progress": 5}]})
    title = window._TitleWatcher(None)._title()
    assert title.startswith("Shorekeep · 5% · ")
    assert title.endswith("…")
    assert len(title) == len("Shorekeep · 5% · ") + 34


def test_title_reports_pause_and_idle(monkeypatch):
    _health(monkeypatch, {"ok": True, "active_jobs": [], "paused": True})
    assert window._TitleWatcher(None)._title() == "Shorekeep (paused)"

    _health(monkeypatch, {"ok": True, "active_jobs": [], "paused": False})
    assert window._TitleWatcher(None)._title() == window.WINDOW_TITLE


def test_title_falls_back_when_the_agent_is_offline(monkeypatch):
    def offline(request, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(window.urllib.request, "urlopen", offline)
    assert window._TitleWatcher(None)._title() == window.WINDOW_TITLE
