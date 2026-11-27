"""Phase 6: update-available check."""
from __future__ import annotations

import json
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from mydl_desktop.updater import _parse_version, check_for_update


def _mock_response(tag: str, html_url: str = "https://github.com/sc8134/ShoreKeep/releases/tag/v9.0.0"):
    body = json.dumps({
        "tag_name": tag,
        "html_url": html_url,
        "published_at": "2026-12-01T00:00:00Z",
    }).encode()

    class FakeResp:
        def read(self):
            return body
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass

    # make json.load work
    resp = MagicMock()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    resp.read = MagicMock(return_value=body)
    # patch json.load to work from the mock
    import io
    resp.__iter__ = MagicMock(return_value=iter([]))
    return resp


def _patch_urlopen(tag: str):
    body = json.dumps({
        "tag_name": tag,
        "html_url": f"https://github.com/sc8134/ShoreKeep/releases/tag/{tag}",
        "published_at": "2026-12-01T00:00:00Z",
    }).encode()

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return body

    # We need json.load(resp) to work, so return a BytesIO-like
    import io
    class ReadableResp(io.RawIOBase):
        def read(self, n=-1): return body
        def readinto(self, b):
            data = body
            n = len(data)
            b[:n] = data
            return n
        def __enter__(self): return self
        def __exit__(self, *a): return False

    return ReadableResp()


def test_parse_version():
    assert _parse_version("0.5.0") == (0, 5, 0)
    assert _parse_version("v1.2.3") == (1, 2, 3)
    assert _parse_version("v0.4.2") == (0, 4, 2)


def test_newer_version_detected():
    with patch("urllib.request.urlopen", return_value=_patch_urlopen("v9.9.9")):
        result = check_for_update("0.5.0")
    assert result is not None
    assert result["version"] == "9.9.9"
    assert "url" in result
    assert "published_at" in result


def test_same_version_returns_none():
    with patch("urllib.request.urlopen", return_value=_patch_urlopen("v0.5.0")):
        result = check_for_update("0.5.0")
    assert result is None


def test_older_version_returns_none():
    with patch("urllib.request.urlopen", return_value=_patch_urlopen("v0.4.0")):
        result = check_for_update("0.5.0")
    assert result is None


def test_network_error_returns_none():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")):
        result = check_for_update("0.5.0")
    assert result is None


def test_malformed_response_returns_none():
    class BadResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"not json {"

    with patch("urllib.request.urlopen", return_value=BadResp()):
        result = check_for_update("0.5.0")
    assert result is None


def test_missing_tag_returns_none():
    import io
    body = json.dumps({"html_url": "https://github.com", "published_at": ""}).encode()
    class Resp(io.RawIOBase):
        def read(self, n=-1): return body
        def readinto(self, b):
            n = len(body)
            b[:n] = body
            return n
        def __enter__(self): return self
        def __exit__(self, *a): return False

    with patch("urllib.request.urlopen", return_value=Resp()):
        result = check_for_update("0.5.0")
    assert result is None
