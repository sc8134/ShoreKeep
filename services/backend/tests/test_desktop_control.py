import threading

import pytest

pytest.importorskip("mydl_desktop")

from mydl_desktop.control import FocusChannel, request_focus


def test_focus_request_reaches_the_channel(tmp_path):
    path = tmp_path / "window.control"
    channel = FocusChannel(path)
    called = threading.Event()
    assert channel.start(called.set)
    try:
        assert request_focus(path, timeout=2.0) is True
        assert called.wait(2.0) is True
    finally:
        channel.close()

    assert path.exists() is False


def test_focus_request_returns_false_without_a_listener(tmp_path):
    path = tmp_path / "window.control"
    path.write_text('{"port": 1, "pid": 999999}', encoding="utf-8")

    assert request_focus(path, timeout=0.2) is False


def test_focus_request_returns_false_for_missing_control_file(tmp_path):
    assert request_focus(tmp_path / "missing.control", timeout=0.2) is False
