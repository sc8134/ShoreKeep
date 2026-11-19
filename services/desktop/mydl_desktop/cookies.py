from __future__ import annotations

import contextlib
import io
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

BROWSER_ORDER = ("firefox", "chrome", "edge", "brave", "chromium")
BROWSER_LABELS = {
    "firefox": "Firefox",
    "chrome": "Chrome",
    "edge": "Edge",
    "brave": "Brave",
    "chromium": "Chromium",
}


class CookieExportError(RuntimeError):
    pass


def _probe_paths() -> dict[str, list[Path]]:
    home = Path.home()
    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        return {
            "firefox": [appdata / "Mozilla" / "Firefox" / "Profiles"],
            "chrome": [local / "Google" / "Chrome" / "User Data"],
            "edge": [local / "Microsoft" / "Edge" / "User Data"],
            "brave": [local / "BraveSoftware" / "Brave-Browser" / "User Data"],
            "chromium": [local / "Chromium" / "User Data"],
        }
    if sys.platform == "darwin":
        support = home / "Library" / "Application Support"
        return {
            "firefox": [support / "Firefox" / "Profiles"],
            "chrome": [support / "Google" / "Chrome"],
            "edge": [support / "Microsoft Edge"],
            "brave": [support / "BraveSoftware" / "Brave-Browser"],
            "chromium": [support / "Chromium"],
        }
    config = home / ".config"
    return {
        "firefox": [home / ".mozilla" / "firefox"],
        "chrome": [config / "google-chrome"],
        "edge": [config / "microsoft-edge"],
        "brave": [config / "BraveSoftware" / "Brave-Browser"],
        "chromium": [config / "chromium"],
    }


def _has_profile(browser: str, roots: list[Path]) -> bool:
    for root in roots:
        if not root.exists():
            continue
        if browser == "firefox":
            if any(child.is_dir() for child in root.iterdir() if child.name != "Crash Reports"):
                return True
            continue
        if (root / "Local State").exists() or (root / "Default").is_dir():
            return True
    return False


def probe_browsers() -> list[dict]:
    paths = _probe_paths()
    result = []
    for browser in BROWSER_ORDER:
        present = _has_profile(browser, paths.get(browser, []))
        note = ""
        if browser == "firefox":
            note = "Recommended: most reliable for automatic cookies"
        elif browser in {"chrome", "edge", "brave", "chromium"} and sys.platform == "win32":
            note = "Chrome 127+ may block automatic cookie reads"
        result.append(
            {
                "key": browser,
                "label": BROWSER_LABELS[browser],
                "present": present,
                "note": note,
            }
        )
    return result


def cookie_candidates(mode: str) -> list[str]:
    """Return browsers to try, in order, for the configured cookie mode."""
    if not mode or mode == "off":
        return []
    present = {item["key"] for item in probe_browsers() if item["present"]}
    if mode in BROWSER_ORDER:
        return [mode] + [b for b in BROWSER_ORDER if b != mode and b in present]
    return [b for b in BROWSER_ORDER if b in present]


def export_cookies(browser: str, dest: Path) -> Path:
    """Copy the user's browser cookies into a Netscape cookies.txt at dest."""
    try:
        from yt_dlp import YoutubeDL
    except Exception as exc:  # pragma: no cover - install issue
        raise CookieExportError("yt-dlp is not installed in the desktop app environment") from exc

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.unlink(missing_ok=True)
    opts = {
        "quiet": True,
        "no_warnings": True,
        "cachedir": False,
        "socket_timeout": 15,
        "cookiesfrombrowser": (browser,),
    }
    try:
        # yt-dlp prints cookie errors to stderr even in quiet mode; keep them out of the log noise.
        with contextlib.redirect_stderr(io.StringIO()):
            with YoutubeDL(opts) as ydl:
                jar = ydl.cookiejar
                if not any(True for _ in jar):
                    raise CookieExportError(f"{BROWSER_LABELS.get(browser, browser)} returned no cookies")
                jar.save(str(dest), ignore_discard=True, ignore_expires=True)
    except CookieExportError:
        raise
    except Exception as exc:
        raise CookieExportError(_clean_error(exc)) from exc

    if not dest.exists() or dest.stat().st_size == 0:
        raise CookieExportError(f"Could not write cookies exported from {browser}")
    try:
        os.chmod(dest, 0o600)
    except OSError:
        pass
    logger.info("exported %d bytes of %s cookies", dest.stat().st_size, browser)
    return dest


def _clean_error(error: BaseException) -> str:
    message = str(error).strip()
    for prefix in ("ERROR: ", "error: "):
        if message.startswith(prefix):
            message = message[len(prefix):]
    lowered = message.lower()
    if "dpapi" in lowered or "app-bound" in lowered or "decrypt" in lowered:
        return (
            "the browser encrypts cookies with App-Bound Encryption; "
            "use Firefox for automatic cookies"
        )
    if "could not copy" in lowered or "database" in lowered:
        return "the browser cookie database is locked; close the browser and retry, or use Firefox"
    return message or error.__class__.__name__
