from __future__ import annotations

import logging
import sys

from .config import AgentConfig

logger = logging.getLogger("mydl_desktop")


def _show_error(log_path) -> None:
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            f"Shorekeep could not start. See {log_path} for details.",
            "Shorekeep",
            0x10,
        )
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    from .agent import _setup_logging, handle_deep_link

    _setup_logging()
    args = list(sys.argv[1:] if argv is None else argv)
    config = AgentConfig.load()
    try:
        if not args:
            from .tray import acquire_single_instance, run_tray

            if not acquire_single_instance():
                return 0
            return run_tray(config)
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
