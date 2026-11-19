from backend.engine.router import direct_plan, is_direct_media_url


def test_detects_direct_media_urls():
    assert is_direct_media_url("https://cdn.example.com/clip.mp4")
    assert is_direct_media_url("https://cdn.example.com/clip.MP4?token=abc")
    assert is_direct_media_url("http://cdn.example.com/song.flac")
    assert not is_direct_media_url("https://example.com/watch?v=1")
    assert not is_direct_media_url("ftp://cdn.example.com/clip.mp4")


def test_direct_plan_audio_preset_converts_video_source():
    plan = direct_plan("https://cdn.example.com/clip.mp4", "audio")

    assert plan.mode == "audio"
    assert plan.ext == "mp3"
    assert plan.engine == "mydl"
    assert plan.video is not None
    assert plan.title == "clip"


def test_direct_plan_audio_source_skips_video():
    plan = direct_plan("https://cdn.example.com/song.flac", "best")

    assert plan.mode == "audio"
    assert plan.audio is not None
    assert plan.video is None
