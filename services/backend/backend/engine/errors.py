from __future__ import annotations

from dataclasses import dataclass

from ..cookies import CookieError
from ..security import UnsafeURLError
from .types import JobTimeout, PlaylistEmpty, SizeLimitExceeded


@dataclass(frozen=True)
class ErrorInfo:
    code: str
    message: str
    retryable: bool = False
    hint: str | None = None


@dataclass(frozen=True)
class _Rule:
    code: str
    tokens: tuple[str, ...]
    message: str
    retryable: bool
    hint: str | None = None


RETRY_HINT = "Retry once. Shorekeep updates yt-dlp weekly to fix site changes."
COOKIE_HINT = "Export a fresh cookies.txt from a signed-in browser and try again."

_RULES: tuple[_Rule, ...] = (
    _Rule(
        "age_restricted",
        ("age-restricted", "age restricted", "confirm your age", "inappropriate for some users"),
        "This video is age-restricted. Add cookies from a signed-in account and retry.",
        True,
        "Sign in on the site, then upload fresh cookies or download with the Shorekeep app.",
    ),
    _Rule(
        "members_only",
        ("members-only", "members only", "join this channel"),
        "This video is available to channel members only.",
        False,
    ),
    _Rule(
        "bot_check",
        ("sign in to confirm", "not a bot", "captcha", "confirm you", "unusual traffic"),
        "The source is blocking automated access right now (bot check). Please retry later.",
        True,
        "Wait a few minutes, then retry. Fresh cookies can help with signed-in sources.",
    ),
    _Rule("drm", ("drm", "widevine"), "DRM-protected content is not supported.", False),
    _Rule(
        "private_video",
        ("private video", "video is private"),
        "This video is private.",
        False,
        "Ask the owner for access, or upload cookies from an account that can watch it.",
    ),
    _Rule(
        "login_required",
        (
            "login required",
            "log in",
            "sign in required",
            "requires login",
            "authenticate",
            "requires authentication",
        ),
        "This site requires a login. Sign in here, add your own cookies.txt, and retry.",
        True,
        COOKIE_HINT,
    ),
    _Rule(
        "live_stream",
        ("live stream", "is live", "live event", "premieres in"),
        "Live streams are not supported yet.",
        False,
    ),
    _Rule(
        "unsupported_url",
        ("unsupported url", "no suitable extractor", "unsupported site", "not a valid url"),
        "This URL is not supported.",
        False,
    ),
    _Rule(
        "geo_blocked",
        ("not available in your country", "geo restricted", "blocked in your country", "geo-blocked"),
        "This video is not available from the server's region.",
        False,
    ),
    _Rule(
        "not_found",
        ("video unavailable", "this video is unavailable", "does not exist", "404", "not found"),
        "This video is unavailable, private, or was removed.",
        False,
    ),
    _Rule(
        "cookies_rejected",
        ("not accepted", "cookie"),
        "Your cookies were rejected or expired. Export a fresh cookies.txt and try again.",
        True,
        COOKIE_HINT,
    ),
    _Rule(
        "network",
        (
            "timed out",
            "timeout",
            "connection reset",
            "connection refused",
            "temporary failure",
            "name resolution",
            "network is unreachable",
            "remote end closed",
            "ssl",
            "tls",
        ),
        "The connection to the source failed. Retry in a moment.",
        True,
        RETRY_HINT,
    ),
)

RETRYABLE_CODES = frozenset(
    {
        "age_restricted",
        "bot_check",
        "cookies_rejected",
        "cookies_unreadable",
        "ffmpeg_missing",
        "login_required",
        "network",
        "timeout",
        "storage_error",
        "unknown",
    }
)


def retryable_code(code: str | None) -> bool:
    return code in RETRYABLE_CODES


def classify_error(error: BaseException) -> ErrorInfo:
    if isinstance(error, JobTimeout):
        return ErrorInfo("timeout", str(error), True, "Try a lower quality or a shorter video.")
    if isinstance(error, SizeLimitExceeded):
        return ErrorInfo("size_limit", str(error), False, "Pick a lower quality or split the playlist.")
    if isinstance(error, PlaylistEmpty):
        return ErrorInfo(
            "playlist_empty",
            str(error) or "This playlist has no downloadable videos.",
            False,
        )
    if isinstance(error, CookieError):
        return ErrorInfo("cookies_unreadable", str(error), True, COOKIE_HINT)
    if isinstance(error, UnsafeURLError):
        return ErrorInfo(
            "unsafe_url",
            "This URL points to a private or local address, which Shorekeep will not fetch.",
            False,
        )

    message = str(error).strip() or error.__class__.__name__
    lowered = message.lower()

    if "fits under the configured size limit" in lowered:
        return ErrorInfo(
            "size_limit",
            "This media is larger than the server's size limit. Pick a lower quality.",
            False,
        )
    if "cookies could not be decrypted" in lowered:
        return ErrorInfo("cookies_unreadable", message, True, COOKIE_HINT)
    if "ffmpeg is not available" in lowered or "ffmpeg failed" in lowered:
        return ErrorInfo(
            "ffmpeg_missing",
            "Media processing (ffmpeg) failed on this worker. Retry once.",
            True,
            RETRY_HINT,
        )
    if any(token in lowered for token in ("no space left", "permission denied", "disk full")):
        return ErrorInfo("storage_error", "The file could not be stored. Retry in a moment.", True, RETRY_HINT)

    for rule in _RULES:
        if rule.code == "cookies_rejected":
            if "cookie" in lowered and any(
                token in lowered
                for token in ("expired", "invalid", "netscape", "rejected", "failed to load", "malformed")
            ):
                return ErrorInfo(rule.code, rule.message, rule.retryable, rule.hint)
            continue
        if any(token in lowered for token in rule.tokens):
            return ErrorInfo(rule.code, rule.message, rule.retryable, rule.hint)

    return ErrorInfo("unknown", message[-700:], True, RETRY_HINT)
