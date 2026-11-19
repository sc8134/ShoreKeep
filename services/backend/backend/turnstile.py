from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from .config import settings

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


class TurnstileError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def is_enabled() -> bool:
    return bool(settings.turnstile_enabled and settings.turnstile_secret)


def verify(token: str | None, remote_ip: str | None = None) -> None:
    """Raise TurnstileError unless the challenge token is valid. No-op when disabled."""
    if not is_enabled():
        return
    if not token:
        raise TurnstileError("Complete the bot check to continue")

    body = urllib.parse.urlencode(
        {"secret": settings.turnstile_secret, "response": token, "remoteip": remote_ip or ""}
    ).encode("utf-8")
    request = urllib.request.Request(
        VERIFY_URL,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.load(response)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise TurnstileError("Bot-check service is unavailable. Try again shortly.", 503) from exc

    if not data.get("success"):
        raise TurnstileError("Bot check failed. Refresh the page and try again.")
