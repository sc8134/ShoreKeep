from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from . import detect
from .config import AGENT_HOST, AGENT_PORT, AgentConfig
from .platform_utils import SingleInstance, open_path

logger = logging.getLogger(__name__)

AGENT_BASE = f"http://{AGENT_HOST}:{AGENT_PORT}"
CONTROL_HEADERS = {"X-Shorekeep-Control": "tray"}
RESTART_WINDOW_SECONDS = 300
MAX_RESTARTS = 3

_instance: SingleInstance | None = None


def acquire_single_instance() -> bool:
    """Return False when another tray instance is already running."""
    global _instance
    if _instance is None:
        _instance = SingleInstance("tray")
    return _instance.acquire()


def icon_image(size: int = 256):
    from importlib.resources import files

    from PIL import Image

    resource = files("mydl_desktop") / "assets" / "shorekeep-tray.png"
    with resource.open("rb") as handle:
        image = Image.open(handle).convert("RGBA")
    return image.resize((size, size), Image.LANCZOS)


def _stop(child: subprocess.Popen | None) -> None:
    if child is None or child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()


def _notify(icon, message: str, title: str = "Shorekeep") -> None:
    try:
        icon.notify(message, title)
    except Exception:
        logger.info("%s: %s", title, message)


def _control(action: str) -> bool:
    request = urllib.request.Request(
        f"{AGENT_BASE}/v1/{action}",
        data=b"",
        headers=CONTROL_HEADERS,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return bool(json.load(response))
    except (urllib.error.URLError, OSError, ValueError):
        logger.exception("could not %s downloads", action)
        return False


def _post_detected(url: str, source: str) -> dict | None:
    payload = json.dumps({"url": url, "source": source}).encode("utf-8")
    request = urllib.request.Request(
        f"{AGENT_BASE}/v1/detected",
        data=payload,
        headers={**CONTROL_HEADERS, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.load(response)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.warning("could not record a detected link: %s", exc)
        return None


class _RestartBudget:
    def __init__(self) -> None:
        self._attempts: dict[str, list[float]] = {}

    def allow(self, kind: str) -> bool:
        now = time.monotonic()
        recent = [stamp for stamp in self._attempts.get(kind, []) if now - stamp < RESTART_WINDOW_SECONDS]
        if len(recent) >= MAX_RESTARTS:
            self._attempts[kind] = recent
            return False
        recent.append(now)
        self._attempts[kind] = recent
        return True


def run_tray(
    config: AgentConfig,
    web_child: subprocess.Popen | None = None,
    window_child: subprocess.Popen | None = None,
) -> int:
    from . import webapp, window
    from .agent import health, start_agent

    child: subprocess.Popen | None = None
    if not health():
        child = start_agent(config)
        if child is None:
            logger.error("the agent did not start; see %s", config.log_path)
    try:
        import pystray
        from pystray import Menu, MenuItem
    except Exception:
        logger.exception("tray support is not installed; the agent keeps running in the background")
        return 0 if health() else 1

    lock = threading.RLock()
    stopping = threading.Event()
    budget = _RestartBudget()
    state = {
        "online": False,
        "paused": False,
        "active": 0,
        "paired": False,
        "jobs": [],
        "last_event": None,
        "last_event_id": None,
        "pending": None,
    }
    initial = health()
    if initial:
        event = initial.get("last_event") or {}
        state.update(
            online=True,
            paused=bool(initial.get("paused")),
            active=int(initial.get("active") or 0),
            paired=bool(initial.get("paired")),
            jobs=list(initial.get("active_jobs") or []),
            last_event=event or None,
            last_event_id=event.get("id"),
            pending=initial.get("pending"),
        )

    def refresh_state(info: dict | None = None) -> None:
        info = info if info is not None else health()
        if info:
            state.update(
                online=True,
                paused=bool(info.get("paused")),
                active=int(info.get("active") or 0),
                paired=bool(info.get("paired")),
                jobs=list(info.get("active_jobs") or []),
                last_event=info.get("last_event"),
                pending=info.get("pending"),
            )
        else:
            state["online"] = False
            state["jobs"] = []

    def average_progress() -> int:
        jobs = state["jobs"]
        if not jobs:
            return 0
        return round(sum(float(job.get("progress") or 0) for job in jobs) / len(jobs))

    def status_text(_item) -> str:
        if not state["online"]:
            return "Agent: starting"
        if state["paused"]:
            if state["active"]:
                return f"Agent: paused ({state['active']} finishing)"
            return "Agent: paused"
        if state["active"]:
            label = "download" if state["active"] == 1 else "downloads"
            return f"Agent: {state['active']} {label} ({average_progress()}%)"
        if not state["paired"]:
            return "Agent: ready (not paired)"
        return "Agent: ready"

    def job_line() -> str:
        jobs = state["jobs"]
        if not jobs:
            return ""
        if len(jobs) == 1:
            job = jobs[0]
            title = str(job.get("title") or "Download").strip()
            if len(title) > 38:
                title = title[:37] + "…"
            return f"{title}: {round(float(job.get('progress') or 0))}%"
        return f"{len(jobs)} downloads in progress: {average_progress()}%"

    def progress_text(_item) -> str:
        return job_line()

    def progress_visible(_item) -> bool:
        return bool(state["jobs"])

    def tooltip() -> str:
        if not state["online"]:
            return "Shorekeep: starting"
        if state["jobs"]:
            return f"Shorekeep: {job_line()}"
        if state["paused"]:
            return "Shorekeep: downloads paused"
        return "Shorekeep"

    def pending_visible(_item) -> bool:
        return bool(state.get("pending"))

    def pending_text(_item) -> str:
        pending = state.get("pending") or {}
        if pending.get("source") == "clipboard":
            return "Review copied link"
        return "Review link from watch folder"

    def on_link(url: str, source: str) -> None:
        entry = _post_detected(url, source)
        if entry is None:
            return
        state["pending"] = entry
        _notify(icon, f"Link detected from {source}. Review it in Shorekeep.")
        try:
            icon.update_menu()
        except Exception:
            logger.exception("could not refresh the tray menu")
        window.focus_running(config)

    def announce(icon) -> None:
        event = state.get("last_event") or {}
        event_id = event.get("id")
        if not event_id or event_id == state.get("last_event_id"):
            return
        state["last_event_id"] = event_id
        title = str(event.get("title") or "Download").strip()
        if event.get("status") == "done":
            _notify(icon, f"Finished: {title}")
        else:
            _notify(icon, f"Failed: {title}")

    def open_folder(_icon, _item) -> None:
        path = config.download_path
        path.mkdir(parents=True, exist_ok=True)
        try:
            open_path(path)
        except Exception:
            logger.exception("could not open %s", path)

    def open_site(_icon, _item) -> None:
        nonlocal window_child
        if window.focus_running(config):
            return
        with lock:
            if window_child is None or window_child.poll() is not None:
                window_child = window.spawn(config)
            current = window_child
        if current is None:
            webbrowser.open(config.server_base)

    def pause_label(_item) -> str:
        return "Resume downloads" if state["paused"] else "Pause downloads"

    def toggle_pause(icon, _item) -> None:
        refresh_state()
        action = "resume" if state["paused"] else "pause"
        if not _control(action):
            _notify(icon, "The desktop agent is not responding. See agent.log.")
            return
        refresh_state()
        if action == "pause":
            if state["active"]:
                _notify(icon, "New downloads are paused. Active downloads will finish.")
            else:
                _notify(icon, "New downloads are paused.")
        else:
            _notify(icon, "Downloads resumed.")
        icon.update_menu()

    def quit_app(icon, _item) -> None:
        stopping.set()
        icon.stop()

    def supervise(icon) -> None:
        nonlocal child, web_child, window_child
        agent_misses = 0
        web_misses = 0
        while not stopping.is_set():
            info = health(timeout=2.0)
            if info:
                agent_misses = 0
                refresh_state(info)
            else:
                agent_misses += 1
                state["online"] = False
                if agent_misses >= 2:
                    agent_misses = 0
                    if budget.allow("agent"):
                        started = start_agent(config)
                        if started is not None:
                            with lock:
                                child = started
                            refresh_state()
                            _notify(icon, "The desktop agent restarted after it stopped.")
                        else:
                            _notify(icon, "The desktop agent is not responding. See agent.log.")
                    else:
                        _notify(icon, "The desktop agent keeps stopping. See agent.log.")

            if webapp.is_local(config):
                if webapp.web_ready(config, timeout=2.0):
                    web_misses = 0
                else:
                    web_misses += 1
                    if web_misses >= 2:
                        web_misses = 0
                        if budget.allow("web"):
                            with lock:
                                _stop(web_child)
                                web_child = None
                            started = webapp.start_webapp(config)
                            with lock:
                                web_child = started
                            if started is not None:
                                _notify(icon, "The Shorekeep web app restarted.")
                            else:
                                _notify(icon, "The Shorekeep web app did not restart. See web.log.")
                        else:
                            _notify(icon, "The Shorekeep web app keeps stopping. See web.log.")

            with lock:
                current = window_child
            if current is not None and current.poll() is not None:
                if current.returncode == 0:
                    with lock:
                        window_child = None
                elif budget.allow("window"):
                    with lock:
                        window_child = window.spawn(config)
                    _notify(icon, "The Shorekeep window restarted after it closed unexpectedly.")
                else:
                    _notify(icon, "The Shorekeep window keeps closing unexpectedly. See window.log.")

            try:
                runner.apply(AgentConfig.load())
            except Exception:
                logger.exception("could not apply the link detection settings")

            icon.title = tooltip()
            announce(icon)
            pending = state.get("pending") or {}
            marker = (state["online"], state["paused"], job_line(), pending.get("id"))
            if state.get("_marker") != marker:
                state["_marker"] = marker
                icon.update_menu()

            stopping.wait(5)

    menu = Menu(
        MenuItem(status_text, None, enabled=False),
        MenuItem(progress_text, None, enabled=False, visible=progress_visible),
        MenuItem(pending_text, open_site, visible=pending_visible),
        MenuItem(pause_label, toggle_pause),
        MenuItem("Open download folder", open_folder),
        MenuItem("Open Shorekeep", open_site),
        Menu.SEPARATOR,
        MenuItem("Quit", quit_app),
    )
    icon = pystray.Icon("shorekeep-desktop", icon_image(64), "Shorekeep", menu)
    runner = detect.DetectionRunner(config)
    runner.configure(on_link)
    runner.apply(config)
    hotkey = None
    if config.hotkey:
        from .hotkey import HotkeyListener

        hotkey = HotkeyListener(config.hotkey, lambda: open_site(icon, None))
        if hotkey.start():
            logger.info("global hotkey %s opens the window", config.hotkey)
    monitor = threading.Thread(target=supervise, args=(icon,), name="tray-supervisor", daemon=True)
    monitor.start()
    try:
        icon.run()
    finally:
        stopping.set()
        runner.stop()
        if hotkey is not None:
            hotkey.stop()
        with lock:
            _stop(window_child)
            _stop(child)
            _stop(web_child)
    return 0
