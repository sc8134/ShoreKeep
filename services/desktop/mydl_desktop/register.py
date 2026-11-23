from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

PROTOCOL = "shorekeep"
WINDOWS_BASE_KEY = rf"Software\Classes\{PROTOCOL}"
LINUX_DESKTOP_NAME = "shorekeep.desktop"
MACOS_APP_NAME = "Shorekeep.app"
MACOS_BUNDLE_ID = "com.shorekeep.desktop"
LSREGISTER = (
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
    "LaunchServices.framework/Support/lsregister"
)


def _python_executable() -> Path:
    exe = Path(sys.executable)
    if exe.name.lower() == "python.exe":
        no_console = exe.with_name("pythonw.exe")
        if no_console.exists():
            return no_console
    return exe


def launch_args() -> list[str]:
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable))]
    return [str(_python_executable()), "-m", "mydl_desktop.agent"]


def _windows_command() -> str:
    args = launch_args()
    return " ".join([f'"{args[0]}"', *args[1:], '"%1"'])


def linux_exec_line() -> str:
    parts = [*launch_args(), "%u"]
    return " ".join(f'"{part}"' if " " in part else part for part in parts)


def linux_desktop_entry(exec_line: str | None = None) -> str:
    return "\n".join(
        [
            "[Desktop Entry]",
            "Type=Application",
            "Name=Shorekeep",
            "Comment=Start the Shorekeep download companion",
            f"Exec={exec_line or linux_exec_line()}",
            "Terminal=false",
            f"MimeType=x-scheme-handler/{PROTOCOL};",
            "Categories=Network;FileTransfer;",
            "NoDisplay=true",
            "",
        ]
    )


def macos_url_types() -> list[dict]:
    return [{"CFBundleURLName": MACOS_BUNDLE_ID, "CFBundleURLSchemes": [PROTOCOL]}]


def _run(command: list[str]) -> bool:
    try:
        result = subprocess.run(command, check=False, capture_output=True)
    except FileNotFoundError:
        return False
    return result.returncode == 0


def _install_windows() -> None:
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, WINDOWS_BASE_KEY) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:Shorekeep Protocol")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, WINDOWS_BASE_KEY + r"\shell\open\command") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, _windows_command())


def _uninstall_windows() -> None:
    import winreg

    for path in (
        WINDOWS_BASE_KEY + r"\shell\open\command",
        WINDOWS_BASE_KEY + r"\shell\open",
        WINDOWS_BASE_KEY + r"\shell",
        WINDOWS_BASE_KEY,
    ):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        except FileNotFoundError:
            pass


def _linux_apps_dir() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / "applications"


def _install_linux() -> None:
    apps = _linux_apps_dir()
    apps.mkdir(parents=True, exist_ok=True)
    target = apps / LINUX_DESKTOP_NAME
    target.write_text(linux_desktop_entry(), encoding="utf-8")
    os.chmod(target, 0o644)
    _run(["xdg-mime", "default", LINUX_DESKTOP_NAME, f"x-scheme-handler/{PROTOCOL}"])
    _run(["update-desktop-database", str(apps)])


def _uninstall_linux() -> None:
    apps = _linux_apps_dir()
    (apps / LINUX_DESKTOP_NAME).unlink(missing_ok=True)
    _run(["update-desktop-database", str(apps)])


def _macos_bundle() -> Path | None:
    for parent in Path(sys.executable).resolve().parents:
        if parent.suffix == ".app":
            return parent
    return None


def _macos_wrapper_app() -> Path:
    return Path.home() / "Applications" / MACOS_APP_NAME


def _macos_applescript() -> str:
    command = shlex.join(launch_args()).replace('"', '\\"')
    return (
        "on open location theURL\n"
        f'    do shell script "{command} " & quoted form of theURL & " > /dev/null 2>&1 &"\n'
        "end open location\n"
    )


def _patch_macos_plist(app: Path) -> None:
    plist = app / "Contents" / "Info.plist"
    data = plistlib.loads(plist.read_bytes())
    data["CFBundleURLTypes"] = macos_url_types()
    data["CFBundleIdentifier"] = MACOS_BUNDLE_ID
    plist.write_bytes(plistlib.dumps(data, fmt=plistlib.FMT_BINARY))


def _register_macos_bundle(bundle: Path) -> None:
    _run([LSREGISTER, "-f", str(bundle)])


def _install_macos() -> None:
    bundle = _macos_bundle()
    if bundle is not None:
        _register_macos_bundle(bundle)
        return

    # Running from source: build a tiny AppleScript app that forwards the URL.
    app = _macos_wrapper_app()
    app.parent.mkdir(parents=True, exist_ok=True)
    if app.exists():
        shutil.rmtree(app)
    try:
        subprocess.run(["osacompile", "-o", str(app), "-e", _macos_applescript()], check=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise SystemExit(
            "macOS URL registration needs osacompile (Xcode command line tools)."
        ) from exc
    _patch_macos_plist(app)
    _register_macos_bundle(app)


def _uninstall_macos() -> None:
    app = _macos_wrapper_app()
    if app.exists():
        _run([LSREGISTER, "-u", str(app)])
        shutil.rmtree(app, ignore_errors=True)


def install() -> None:
    if sys.platform == "win32":
        _install_windows()
    elif sys.platform == "darwin":
        _install_macos()
    else:
        _install_linux()


def uninstall() -> None:
    if sys.platform == "win32":
        _uninstall_windows()
    elif sys.platform == "darwin":
        _uninstall_macos()
    else:
        _uninstall_linux()
