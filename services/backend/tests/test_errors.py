from backend.cookies import CookieError
from backend.engine.errors import classify_error, retryable_code
from backend.engine.types import ExtractionError, JobTimeout, PlaylistEmpty, SizeLimitExceeded
from backend.security import UnsafeURLError


def test_timeout_and_size_limits():
    timeout = classify_error(JobTimeout("Job exceeded the 60s time limit"))
    assert timeout.code == "timeout"
    assert timeout.retryable is True

    size = classify_error(SizeLimitExceeded("File is larger than the 10 MB limit"))
    assert size.code == "size_limit"
    assert size.retryable is False


def test_playlist_empty_and_unsafe_urls():
    assert classify_error(PlaylistEmpty("")).code == "playlist_empty"
    assert classify_error(PlaylistEmpty("")).message
    unsafe = classify_error(UnsafeURLError("Refusing to fetch 127.0.0.1"))
    assert unsafe.code == "unsafe_url"
    assert unsafe.retryable is False


def test_cookie_errors_are_retryable():
    assert classify_error(CookieError("Stored cookies could not be decrypted")).code == (
        "cookies_unreadable"
    )
    rejected = classify_error(
        RuntimeError("Your cookies were rejected or expired. Export a fresh cookies.txt")
    )
    assert rejected.code == "cookies_rejected"
    assert rejected.retryable is True


def test_age_restriction_wins_over_bot_check():
    info = classify_error(RuntimeError("ERROR: Sign in to confirm your age"))
    assert info.code == "age_restricted"


def test_common_ytdlp_failures():
    cases = {
        "ERROR: Sign in to confirm you're not a bot": "bot_check",
        "ERROR: [youtube] abc: Private video": "private_video",
        "ERROR: This video is DRM protected": "drm",
        "ERROR: Unsupported URL: https://example.com/x": "unsupported_url",
        "ERROR: This video is not available in your country": "geo_blocked",
        "ERROR: HTTP Error 404: Not Found": "not_found",
        "ERROR: The read operation timed out": "network",
    }
    for message, code in cases.items():
        assert classify_error(RuntimeError(message)).code == code, message


def test_unknown_errors_keep_the_message_and_are_retryable():
    info = classify_error(RuntimeError("something unexpected happened"))
    assert info.code == "unknown"
    assert info.message == "something unexpected happened"
    assert info.retryable is True
    assert info.hint


def test_size_limit_extraction_errors_are_not_retryable():
    info = classify_error(
        ExtractionError("No audio track fits under the configured size limit")
    )
    assert info.code == "size_limit"
    assert info.retryable is False


def test_retryable_code_lookup():
    assert retryable_code("timeout") is True
    assert retryable_code("ffmpeg_missing") is True
    assert retryable_code("size_limit") is False
    assert retryable_code(None) is False
