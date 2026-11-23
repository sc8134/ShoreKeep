# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]
DESKTOP = ROOT / "services" / "desktop"
BACKEND = ROOT / "services" / "backend"
ICON = ROOT / "assets" / "brand" / "shorekeep-256.png"

hiddenimports: list[str] = []
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("mydl")
hiddenimports += collect_submodules("yt_dlp")
hiddenimports += collect_submodules("mydl_desktop")
hiddenimports += collect_submodules("backend")
hiddenimports += collect_submodules("webview")
hiddenimports += collect_submodules("sqlalchemy.dialects.sqlite")
hiddenimports += ["pystray._xorg", "httptools", "h11"]

datas, binaries, ffmpeg_hidden = collect_all("imageio_ffmpeg")
datas += collect_data_files("mydl_desktop")
datas += collect_data_files("backend")
hiddenimports += ffmpeg_hidden

a = Analysis(
    [str(ROOT / "infra" / "entry.py")],
    pathex=[str(DESKTOP), str(BACKEND)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest"],
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Shorekeep",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ICON) if ICON.exists() else None,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Shorekeep",
)
