from __future__ import annotations

import logging
import subprocess
import sys
import time
import webbrowser

from .config import AgentConfig

logger = logging.getLogger("mydl_desktop")


def _show_error(log_path) -> None:
    text = f"Shorekeep could not start. See {log_path} for details."
    try:
        if sys.platform == "win32" and getattr(sys, "frozen", False):
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, text, "Shorekeep", 0x10)
        elif sys.platform == "darwin":
            safe = text.replace('"', '\\"')
            script = (
                f'display dialog "{safe}" with title "Shorekeep" '
                'buttons {"OK"} default button "OK"'
            )
            subprocess.run(["osascript", "-e", script], check=False, capture_output=True)
        else:
            subprocess.run(
                ["zenity", "--error", "--title", "Shorekeep", "--text", text],
                check=False,
                capture_output=True,
            )
    except Exception:
        pass


def _open_ui(config: AgentConfig) -> None:
    try:
        webbrowser.open(config.server_base)
    except Exception:
        logger.exception("could not open %s", config.server_base)


def _open_window(config: AgentConfig):
    from . import window

    child = window.spawn(config)
    if child is None:
        return None
    # WebView2 can fail to initialize while a previous instance is still shutting
    # down (for example right after an installer upgrade); retry once.
    time.sleep(2.0)
    if child.poll() is not None:
        time.sleep(2.0)
        child = window.spawn(config)
    return child


def main(argv: list[str] | None = None) -> int:
    from .agent import _setup_logging, handle_deep_link

    args = list(sys.argv[1:] if argv is None else argv)
    config = AgentConfig.load()
    minimized = "--minimized" in args
    args = [arg for arg in args if arg != "--minimized"]

    if args == ["--web"]:
        from .webapp import serve

        return serve(config)

    if args == ["--window"]:
        from .window import main as window_main

        return window_main(config)

    _setup_logging()
    try:
        if not args:
            from . import webapp
            from .tray import acquire_single_instance, run_tray

            if not acquire_single_instance():
                if not minimized and _open_window(config) is None:
                    _open_ui(config)
                return 0
            web_child = webapp.ensure_webapp(config)
            if webapp.is_local(config) and web_child is None and not webapp.web_ready(config):
                logger.error("the web app did not start; see %s", config.config_path.parent / "web.log")
                _show_error(config.config_path.parent / "web.log")
                return 1
            window_child = None
            if not minimized:
                window_child = _open_window(config)
                if window_child is None:
                    _open_ui(config)
            return run_tray(config, web_child=web_child, window_child=window_child)
        if len(args) == 1 and "://" in args[0]:
            return handle_deep_link(args[0], config)
        from .agent import main as cli_main

        return cli_main(args)
    except Exception:
        logger.exception("launcher failed")
        _show_error(config.log_path)
        return 1


if __name__ == "__main__":
    sys.exit(main())
