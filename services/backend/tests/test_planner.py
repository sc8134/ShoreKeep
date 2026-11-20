import pytest

from backend.engine.extract import build_plan
from backend.engine.types import ExtractionError


def make_format(**overrides) -> dict:
    fmt = {
        "format_id": "f1",
        "url": "https://cdn.example.com/f1.mp4",
        "protocol": "https",
        "ext": "mp4",
        "vcodec": "avc1.64001f",
        "acodec": "mp4a.40.2",
        "height": 720,
        "tbr": 1000.0,
        "filesize": 5_000_000,
    }
    fmt.update(overrides)
    return fmt


def make_info(*formats, **overrides) -> dict:
    info = {
        "_type": "video",
        "title": "Example clip",
        "webpage_url": "https://example.com/watch?v=1",
        "duration": 60,
        "thumbnail": "https://example.com/thumb.jpg",
        "formats": list(formats),
    }
    info.update(overrides)
    return info


def test_progressive_preferred_when_it_matches_best_height():
    progressive = make_format(format_id="18", height=720)
    video_only = make_format(format_id="137", acodec=None, height=1080, filesize=9_000_000)
    audio = make_format(format_id="140", vcodec=None, acodec="mp4a", ext="m4a", height=None, filesize=1_000_000)

    plan = build_plan(make_info(progressive, video_only, audio), "best")

    assert plan.mode == "merge"
    assert plan.engine == "mydl"
    assert plan.ext == "mp4"
    assert plan.video.format_id == "137"
    assert plan.audio.format_id == "140"
    assert plan.selector == "137+140"


def test_progressive_stream_used_when_quality_cap_met():
    progressive = make_format(format_id="22", height=720)
    video_only = make_format(format_id="137", acodec=None, height=1080)

    plan = build_plan(make_info(progressive, video_only), "720p")

    assert plan.mode == "progressive"
    assert plan.engine == "mydl"
    assert plan.video.format_id == "22"


def test_hls_stream_routes_to_ytdlp():
    hls_video = make_format(
        format_id="hls-1080",
        protocol="m3u8_native",
        acodec=None,
        height=1080,
        url="https://cdn.example.com/master.m3u8",
    )
    audio = make_format(format_id="140", vcodec=None, acodec="mp4a", ext="m4a", height=None)

    plan = build_plan(make_info(hls_video, audio), "best")

    assert plan.engine == "ytdlp"
    assert plan.mode == "merge"
    assert plan.selector == "hls-1080+140"


def test_hls_audio_renditions_without_codecs_are_detected():
    def audio_rendition(name: str) -> dict:
        return make_format(
            format_id=f"hls-audio0-{name}",
            url=f"https://cdn.example.com/audio-{name}.m3u8",
            protocol="m3u8_native",
            vcodec="none",
            acodec=None,
            height=None,
            tbr=None,
            filesize=None,
        )

    plan = build_plan(make_info(audio_rendition("low"), audio_rendition("medium")), "audio")

    assert plan.engine == "ytdlp"
    assert plan.audio.format_id == "hls-audio0-medium"


def test_audio_preset_selects_best_audio():
    audio_a = make_format(format_id="139", vcodec=None, acodec="mp4a", ext="m4a", height=None, tbr=48)
    audio_b = make_format(format_id="140", vcodec=None, acodec="mp4a", ext="m4a", height=None, tbr=128)

    plan = build_plan(make_info(audio_a, audio_b), "audio")

    assert plan.mode == "audio"
    assert plan.ext == "mp3"
    assert plan.engine == "mydl"
    assert plan.audio.format_id == "140"


def test_drm_content_rejected():
    with pytest.raises(ExtractionError):
        build_plan(make_info(make_format(has_drm=True)), "best")


def test_playlist_rejected():
    with pytest.raises(ExtractionError):
        build_plan(make_info(make_format(), _type="playlist", entries=[{"id": "1"}]), "best")


def test_size_limit_rejects_everything():
    with pytest.raises(ExtractionError):
        build_plan(make_info(make_format(filesize=5_000_000)), "best", max_bytes=100)


def test_quality_cap_falls_back_to_lowest_available():
    plan = build_plan(make_info(make_format(format_id="p720", height=720)), "480p")

    assert plan.mode == "progressive"
    assert plan.video.height == 720


def test_webm_streams_merge_into_webm():
    video = make_format(format_id="v", ext="webm", vcodec="vp9", acodec=None, height=1080)
    audio = make_format(format_id="a", ext="webm", vcodec=None, acodec="opus", height=None)

    plan = build_plan(make_info(video, audio), "best")

    assert plan.mode == "merge"
    assert plan.ext == "webm"
