from __future__ import annotations

import sys
from pathlib import Path

PROTOCOL = "shorekeep"
BASE_KEY = rf"Software\Classes\{PROTOCOL}"


def _command() -> str:
    exe = Path(sys.executable)
    if exe.name.lower() != "pythonw.exe":
        no_console = exe.with_name("pythonw.exe")
        if no_console.exists():
            exe = no_console
    return f'"{exe}" -m mydl_desktop.agent "%1"'


def install() -> None:
    if sys.platform != "win32":
        raise SystemExit(
            "Protocol registration is implemented for Windows only. "
            "On macOS/Linux, start the agent with: python -m mydl_desktop.agent --serve"
        )
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, BASE_KEY) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:Shorekeep Protocol")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, BASE_KEY + r"\shell\open\command") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, _command())


def uninstall() -> None:
    if sys.platform != "win32":
        raise SystemExit("Protocol registration is implemented for Windows only.")
    import winreg

    for path in (
        BASE_KEY + r"\shell\open\command",
        BASE_KEY + r"\shell\open",
        BASE_KEY + r"\shell",
        BASE_KEY,
    ):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        except FileNotFoundError:
            pass
