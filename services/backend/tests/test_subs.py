import pytest

from backend.engine import subs
from backend.engine.subs import (
    chapters_from,
    chapters_text,
    download_subtitle,
    format_timestamp,
    pick_subtitle,
    safe_lang,
)
from backend.security import UnsafeURLError


def test_picks_manual_english_vtt_first():
    info = {
        "subtitles": {
            "es": [{"ext": "vtt", "url": "https://subs.example/es.vtt"}],
            "en": [{"ext": "vtt", "url": "https://subs.example/en.vtt"}],
        },
        "automatic_captions": {
            "en": [{"ext": "json3", "url": "https://subs.example/auto.json3"}],
        },
    }
    lang, fmt = pick_subtitle(info)
    assert lang == "en"
    assert fmt["url"].endswith("en.vtt")


def test_falls_back_to_automatic_captions():
    info = {
        "subtitles": {},
        "automatic_captions": {"de": [{"ext": "vtt", "url": "https://subs.example/de.vtt"}]},
    }
    assert pick_subtitle(info)[0] == "de"


def test_ignores_non_vtt_tracks():
    info = {"subtitles": {"en": [{"ext": "json3", "url": "https://subs.example/en.json3"}]}}
    assert pick_subtitle(info) is None
    assert pick_subtitle({}) is None


def test_chapters_and_timestamps():
    info = {
        "chapters": [
            {"start_time": 0, "end_time": 60, "title": "Intro"},
            {"start_time": 3725.4, "end_time": 3800, "title": "Wrap up"},
            {"start_time": 10},
        ]
    }
    chapters = chapters_from(info)
    assert chapters == [
        {"start": 0.0, "title": "Intro"},
        {"start": 3725.4, "title": "Wrap up"},
    ]
    assert format_timestamp(0) == "0:00"
    assert format_timestamp(65) == "1:05"
    assert format_timestamp(3725) == "1:02:05"
    assert chapters_text(chapters).splitlines()[1] == "1:02:05  Wrap up"


def test_safe_lang():
    assert safe_lang("en-US") == "en-US"
    assert safe_lang("../etc/passwd") == "etcpasswd"
    assert safe_lang("") == "subs"


def test_hls_subtitle_playlist_is_merged(tmp_path, monkeypatch):
    playlist = (
        "#EXTM3U\n"
        "#EXT-X-TARGETDURATION:30\n"
        "#EXTINF:30.0,\n"
        "en.vtt?segment=0&duration=30\n"
        "#EXTINF:30.0,\n"
        "en.vtt?segment=1&duration=30\n"
    )
    sources = {
        "https://hls.example/subs/en.m3u8": playlist.encode("utf-8"),
        "https://hls.example/subs/en.vtt?segment=0&duration=30": (
            "WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:0\n\n"
            "00:00:01.000 --> 00:00:02.000\nfirst\n"
        ).encode("utf-8"),
        "https://hls.example/subs/en.vtt?segment=1&duration=30": (
            "WEBVTT\n\n00:00:31.000 --> 00:00:32.000\nsecond\n"
        ).encode("utf-8"),
    }
    monkeypatch.setattr(subs, "assert_public_http_url", lambda url: None)
    monkeypatch.setattr(subs, "_read", lambda url, headers, limit: sources[url])

    dest = download_subtitle(
        {"ext": "vtt", "url": "https://hls.example/subs/en.m3u8"}, tmp_path / "en.vtt"
    )

    text = dest.read_text(encoding="utf-8")
    assert text.startswith("WEBVTT\n\n")
    assert "first" in text and "second" in text
    assert text.count("WEBVTT") == 1
    assert "X-TIMESTAMP-MAP" not in text
    assert text.index("first") < text.index("second")


def test_encrypted_subtitle_playlist_is_rejected(tmp_path, monkeypatch):
    playlist = (
        "#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI=\"key.bin\"\n#EXTINF:30.0,\nseg.vtt\n"
    )
    monkeypatch.setattr(subs, "assert_public_http_url", lambda url: None)
    monkeypatch.setattr(subs, "_read", lambda url, headers, limit: playlist.encode("utf-8"))

    with pytest.raises(ValueError, match="Encrypted"):
        download_subtitle(
            {"ext": "vtt", "url": "https://hls.example/subs/en.m3u8"}, tmp_path / "en.vtt"
        )


class _FakeResponse:
    def __init__(self, data: bytes, url: str) -> None:
        self._data = data
        self._url = url

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def read(self, size: int) -> bytes:
        return self._data[:size]

    def geturl(self) -> str:
        return self._url


def test_subtitle_redirects_to_private_addresses_are_blocked(tmp_path, monkeypatch):
    def guard(url: str) -> None:
        if "127.0.0.1" in url:
            raise UnsafeURLError("private address")

    monkeypatch.setattr(subs, "assert_public_http_url", guard)
    monkeypatch.setattr(
        subs.urllib.request,
        "urlopen",
        lambda request, timeout: _FakeResponse(b"WEBVTT\n", "http://127.0.0.1/internal"),
    )

    with pytest.raises(UnsafeURLError):
        download_subtitle(
            {"ext": "vtt", "url": "https://public.example/en.vtt"}, tmp_path / "en.vtt"
        )
