# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parents[1]
APP_DIR = ROOT / "dist" / "Shorekeep"
ICON = ROOT / "assets" / "brand" / "shorekeep.ico"
VERSION_FILE = ROOT / "infra" / "windows" / "version_info.txt"
VERSION = os.environ.get("SHOREKEEP_VERSION") or (ROOT / "VERSION").read_text(encoding="utf-8").strip()

if not (APP_DIR / "Shorekeep.exe").exists():
    raise SystemExit("Build dist\\Shorekeep first with Shorekeep.spec")

a = Analysis(
    [str(Path(SPECPATH) / "setup_entry.py")],
    pathex=[],
    binaries=[],
    datas=[(str(APP_DIR), "payload"), (str(ROOT / "VERSION"), ".")],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=f"Shorekeep-Setup-{VERSION}",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON),
    version=str(VERSION_FILE),
)
