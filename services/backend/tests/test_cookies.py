import time

import pytest

from backend.cookies import (
    CookieError,
    apply_to_plan,
    cookies_to_header,
    decrypt_cookies,
    encrypt_cookies,
    parse_cookies,
    validate_cookies,
)
from backend.engine.types import MediaPlan, Stream

SAMPLE = (
    "# Netscape HTTP Cookie File\n"
    ".youtube.com\tTRUE\t/\tTRUE\t{exp}\tSID\tabc123\n"
    ".youtube.com\tTRUE\t/\tFALSE\t{exp}\tPREF\tf6=400\n"
    ".other.com\tTRUE\t/\tFALSE\t{exp}\tOTHER\tnope\n"
)


def sample(expires: int | None = None) -> str:
    exp = int(time.time()) + 3600 if expires is None else expires
    return SAMPLE.format(exp=exp)


def test_validate_and_parse():
    text = validate_cookies(sample())
    cookies = parse_cookies(text)
    assert {cookie.name for cookie in cookies} == {"SID", "PREF", "OTHER"}


def test_validate_rejects_garbage_and_binary():
    for bad in ("", "   \n", "just some text\n", "# comment only\n", "a\x00b"):
        with pytest.raises(CookieError):
            validate_cookies(bad)


def test_validate_enforces_size(monkeypatch):
    from backend.config import settings

    monkeypatch.setattr(settings, "max_cookie_kb", 1)
    with pytest.raises(CookieError):
        validate_cookies(sample() + "#" + "x" * 2048 + "\n")


def test_header_matches_domain_and_skips_expired():
    header = cookies_to_header(sample(), "https://www.youtube.com/watch?v=1")
    assert header is not None
    assert "SID=abc123" in header
    assert "OTHER" not in header

    expired = sample(expires=int(time.time()) - 60)
    assert cookies_to_header(expired, "https://www.youtube.com/watch?v=1") is None


def test_header_respects_secure_flag():
    text = ".example.com\tTRUE\t/\tTRUE\t0\tS\tonly-https\n"
    assert cookies_to_header(text, "http://example.com/v.mp4") is None
    assert cookies_to_header(text, "https://example.com/v.mp4") == "S=only-https"


def test_encrypt_roundtrip_and_tamper():
    text = sample()
    token = encrypt_cookies(text)
    assert token != text
    assert decrypt_cookies(token) == text
    with pytest.raises(CookieError):
        decrypt_cookies("not-a-valid-token")


def test_apply_to_plan_sets_matching_header():
    plan = MediaPlan(
        title="t",
        preset="720p",
        mode="progressive",
        ext="mp4",
        webpage_url="https://www.youtube.com/watch?v=1",
        engine="mydl",
        video=Stream(url="https://rr1.googlevideo.com/v.mp4", protocol="https", ext="mp4"),
    )
    apply_to_plan(plan, ".googlevideo.com\tTRUE\t/\tFALSE\t0\tSIG\ttoken\n")
    assert plan.video.http_headers["Cookie"] == "SIG=token"


def test_apply_to_plan_keeps_existing_cookie_header():
    plan = MediaPlan(
        title="t",
        preset="720p",
        mode="progressive",
        ext="mp4",
        webpage_url="https://example.com/v",
        engine="mydl",
        video=Stream(
            url="https://www.youtube.com/v.mp4",
            protocol="https",
            ext="mp4",
            http_headers={"Cookie": "already=set"},
        ),
    )
    apply_to_plan(plan, sample())
    assert plan.video.http_headers["Cookie"] == "already=set"
