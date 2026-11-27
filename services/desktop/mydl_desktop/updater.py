from __future__ import annotations

import logging
import urllib.error
import urllib.request
import json
from typing import Any

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com/repos/{repo}/releases/latest"


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse a version string like '0.5.0' or 'v0.5.0' into a comparable tuple."""
    cleaned = v.lstrip("v").strip()
    try:
        return tuple(int(x) for x in cleaned.split("."))
    except (ValueError, AttributeError):
        return (0,)


def check_for_update(
    current_version: str,
    repo: str = "sc8134/ShoreKeep",
) -> dict[str, Any] | None:
    """Check GitHub for a newer release.

    Returns {version, url, published_at} if a newer version is available,
    None otherwise. Never raises — all errors are logged and swallowed.
    """
    try:
        url = _GITHUB_API.format(repo=repo)
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "shorekeep-desktop"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data: dict[str, Any] = json.load(resp)
        tag: str = data.get("tag_name") or ""
        if not tag:
            return None
        latest = _parse_version(tag)
        current = _parse_version(current_version)
        if latest <= current:
            return None
        html_url: str = data.get("html_url") or f"https://github.com/{repo}/releases"
        published_at: str = data.get("published_at") or ""
        return {
            "version": tag.lstrip("v"),
            "url": html_url,
            "published_at": published_at,
        }
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        logger.debug("update check failed: %s", exc)
        return None
