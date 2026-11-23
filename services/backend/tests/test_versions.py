from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def _pyproject_version(relative: str) -> str:
    with (ROOT / relative).open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def test_backend_version_matches_root():
    from backend import __version__ as backend_version

    assert VERSION
    assert backend_version == VERSION
    assert _pyproject_version("services/backend/pyproject.toml") == VERSION


def test_desktop_version_matches_root():
    assert _pyproject_version("services/desktop/pyproject.toml") == VERSION
    if importlib.util.find_spec("mydl_desktop") is not None:
        from mydl_desktop import __version__ as desktop_version

        assert desktop_version == VERSION


def test_windows_build_version_matches_root():
    major, minor, patch = (int(part) for part in VERSION.split(".")[:3])
    info = (ROOT / "infra/windows/version_info.txt").read_text(encoding="utf-8")
    assert f"filevers=({major}, {minor}, {patch}, 0)" in info
    assert f"prodvers=({major}, {minor}, {patch}, 0)" in info
    assert f"StringStruct('FileVersion', '{VERSION}')" in info
    assert f"StringStruct('ProductVersion', '{VERSION}')" in info

    iss = (ROOT / "infra/windows/Shorekeep.iss").read_text(encoding="utf-8")
    assert f'#define AppVersion "{VERSION}"' in iss


def test_build_specs_share_the_real_entrypoint():
    assert (ROOT / "infra" / "entry.py").is_file()
    for platform in ("windows", "macos", "linux"):
        spec = (ROOT / "infra" / platform / "Shorekeep.spec").read_text(encoding="utf-8")
        assert 'ROOT / "infra" / "entry.py"' in spec
