from __future__ import annotations

import base64
import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken

from .config import settings
from .engine.types import MediaPlan


class CookieError(ValueError):
    pass


@dataclass(frozen=True)
class Cookie:
    domain: str
    path: str
    secure: bool
    expires: int
    name: str
    value: str


def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.secret_key.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_cookies(text: str) -> str:
    return _fernet().encrypt(text.encode("utf-8")).decode("ascii")


def decrypt_cookies(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise CookieError("Stored cookies could not be decrypted") from exc


def validate_cookies(text: str) -> str:
    """Validate an uploaded Netscape cookies.txt and return the normalized text."""
    if not text or not text.strip():
        raise CookieError("The cookies file is empty")
    if "\x00" in text:
        raise CookieError("The cookies file contains binary data; export it as Netscape cookies.txt")
    size = len(text.encode("utf-8"))
    limit = settings.max_cookie_kb * 1024
    if size > limit:
        raise CookieError(f"The cookies file is larger than {settings.max_cookie_kb} KB")
    if not parse_cookies(text):
        raise CookieError(
            "No cookies found. Export the site's cookies in Netscape format (cookies.txt)."
        )
    return text.strip() + "\n"


def parse_cookies(text: str) -> list[Cookie]:
    cookies: list[Cookie] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        http_only = line.startswith("#HttpOnly_")
        if line.startswith("#") and not http_only:
            continue
        if http_only:
            line = line[len("#HttpOnly_"):]
        fields = line.split("\t")
        if len(fields) != 7:
            continue
        domain, _flag, path, secure, expires, name, value = fields
        if not domain or not name:
            continue
        try:
            expiry = int(expires)
        except ValueError:
            continue
        cookies.append(
            Cookie(
                domain=domain,
                path=path or "/",
                secure=secure.upper() == "TRUE",
                expires=expiry,
                name=name,
                value=value,
            )
        )
    return cookies


def _domain_matches(cookie_domain: str, host: str) -> bool:
    domain = cookie_domain.lstrip(".").lower()
    host = host.lower()
    return host == domain or host.endswith("." + domain)


def cookies_to_header(text: str, url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return None
    path = parsed.path or "/"
    is_https = parsed.scheme == "https"
    now = time.time()
    pairs: list[str] = []
    for cookie in parse_cookies(text):
        if not _domain_matches(cookie.domain, host):
            continue
        if not path.startswith(cookie.path):
            continue
        if cookie.secure and not is_https:
            continue
        if cookie.expires and cookie.expires < now:
            continue
        pairs.append(f"{cookie.name}={cookie.value}")
    return "; ".join(pairs) if pairs else None


def apply_to_plan(plan: MediaPlan, text: str) -> None:
    """Attach a matching Cookie header to MY-DL streams (yt-dlp reads the cookiefile itself)."""
    for stream in (plan.video, plan.audio):
        if stream is None:
            continue
        if any(key.lower() == "cookie" for key in stream.http_headers):
            continue
        header = cookies_to_header(text, stream.url)
        if header:
            stream.http_headers["Cookie"] = header


def write_cookie_file(text: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path
