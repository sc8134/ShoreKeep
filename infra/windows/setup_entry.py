from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
import winreg
from pathlib import Path

APP_NAME = "Shorekeep"
APP_EXE = "Shorekeep.exe"
VERSION = "0.3.0"
CREATE_NO_WINDOW = 0x08000000
MB_OK = 0x0
MB_YESNO = 0x4
MB_ICONERROR = 0x10
MB_ICONQUESTION = 0x20
MB_ICONINFORMATION = 0x40

UNINSTALL_PS1 = r"""$ErrorActionPreference = "SilentlyContinue"
Get-Process Shorekeep | Stop-Process -Force
Start-Sleep -Milliseconds 400
Set-Location $env:TEMP
$app = $PSScriptRoot
Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'Shorekeep'
Remove-Item -Path 'HKCU:\Software\Classes\shorekeep' -Recurse -Force
Remove-Item -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Shorekeep' -Recurse -Force
Remove-Item -Path (Join-Path ([Environment]::GetFolderPath('Programs')) 'Shorekeep.lnk') -Force
$desktop = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Shorekeep.lnk'
Remove-Item -Path $desktop -Force
Start-Sleep -Milliseconds 300
Remove-Item -Path $app -Recurse -Force
"""


def log(message: str) -> None:
    try:
        with open(Path(tempfile.gettempdir()) / "shorekeep-setup.log", "a", encoding="utf-8") as handle:
            handle.write(message + "\n")
    except OSError:
        pass


def say(text: str, flags: int = MB_ICONINFORMATION) -> None:
    ctypes.windll.user32.MessageBoxW(None, text, "Shorekeep Setup", MB_OK | flags)


def ask(text: str) -> bool:
    return ctypes.windll.user32.MessageBoxW(None, text, "Shorekeep Setup", MB_YESNO | MB_ICONQUESTION) == 6


def target_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        raise RuntimeError("LOCALAPPDATA is not set")
    return Path(base) / "Programs" / "Shorekeep"


def payload_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "payload"


def app_running() -> bool:
    result = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {APP_EXE}", "/NH"],
        capture_output=True,
        text=True,
        creationflags=CREATE_NO_WINDOW,
    )
    return APP_EXE.lower() in result.stdout.lower()


def stop_app() -> None:
    subprocess.run(
        ["taskkill", "/IM", APP_EXE, "/T", "/F"],
        capture_output=True,
        creationflags=CREATE_NO_WINDOW,
    )
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if not app_running():
            return
        time.sleep(0.5)


def register_protocol(exe: Path) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\shorekeep") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:Shorekeep Protocol")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(
        winreg.HKEY_CURRENT_USER, r"Software\Classes\shorekeep\shell\open\command"
    ) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{exe}" "%1"')


def set_autostart(exe: Path, enabled: bool) -> None:
    run_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, f'"{exe}" --minimized')
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass


def create_shortcut(exe: Path) -> None:
    script = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:MYDL_LINK);"
        "$s.TargetPath=$env:MYDL_EXE;"
        "$s.WorkingDirectory=$env:MYDL_DIR;"
        "$s.IconLocation=$env:MYDL_EXE + ',0';"
        "$s.Save()"
    )
    env = dict(
        os.environ,
        MYDL_LINK=str(start_menu_link()),
        MYDL_EXE=str(exe),
        MYDL_DIR=str(exe.parent),
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
        capture_output=True,
        env=env,
        creationflags=CREATE_NO_WINDOW,
        check=False,
    )
    if not start_menu_link().exists():
        log(f"shortcut not created at {start_menu_link()}")


def start_menu_link() -> Path:
    return (
        Path(os.environ["APPDATA"])
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / f"{APP_NAME}.lnk"
    )


def write_uninstaller(dest: Path) -> None:
    (dest / "uninstall.ps1").write_text(UNINSTALL_PS1, encoding="utf-8")


def register_uninstall_entry(dest: Path, exe: Path) -> None:
    command = (
        "powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass "
        f'-File "{dest / "uninstall.ps1"}"'
    )
    with winreg.CreateKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Shorekeep",
    ) as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
        winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, VERSION)
        winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, "Shorekeep")
        winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, str(exe))
        winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(dest))
        winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, command)
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)


def install(silent: bool, no_autostart: bool) -> int:
    dest = target_dir()
    source = payload_root()
    if not (source / APP_EXE).exists():
        raise RuntimeError("the setup payload is missing Shorekeep.exe")

    if app_running():
        if silent:
            stop_app()
        elif not ask("Shorekeep is running. Close it and continue?"):
            return 1
        else:
            stop_app()

    if not silent:
        if not ask(
            f"Install {APP_NAME} {VERSION} for this Windows user?\n\n"
            f"Location: {dest}\n\n"
            "This is an unsigned development build. Windows may show a SmartScreen warning."
        ):
            return 1

    log(f"installing {VERSION} to {dest}")
    if dest.exists():
        for attempt in range(5):
            try:
                shutil.rmtree(dest)
                break
            except OSError:
                if attempt == 4:
                    raise
                time.sleep(1)
    shutil.copytree(source, dest)

    exe = dest / APP_EXE
    register_protocol(exe)
    autostart = not no_autostart and (silent or ask("Start Shorekeep automatically when you sign in?"))
    set_autostart(exe, autostart)
    create_shortcut(exe)
    write_uninstaller(dest)
    register_uninstall_entry(dest, exe)

    if not silent:
        say(
            f"{APP_NAME} {VERSION} is installed.\n\n"
            "Starting it now. Look for the Shorekeep icon near the clock."
        )
    subprocess.Popen([str(exe)], cwd=str(dest), creationflags=CREATE_NO_WINDOW)
    return 0


def main(argv: list[str]) -> int:
    silent = "--silent" in argv
    no_autostart = "--no-autostart" in argv
    try:
        code = install(silent, no_autostart)
        log(f"exit {code}")
        return code
    except Exception as exc:
        log(f"failed: {exc}")
        if not silent:
            say(f"Install failed:\n\n{exc}", MB_ICONERROR)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
