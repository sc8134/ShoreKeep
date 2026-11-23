import pytest

pytest.importorskip("mydl_desktop")

from mydl_desktop import register


def test_linux_desktop_entry_registers_scheme():
    entry = register.linux_desktop_entry('"/opt/Shorekeep" %u')

    assert "Type=Application" in entry
    assert 'Exec="/opt/Shorekeep" %u' in entry
    assert "MimeType=x-scheme-handler/shorekeep;" in entry


def test_linux_exec_line_quotes_frozen_path_with_spaces(monkeypatch):
    monkeypatch.setattr(register.sys, "frozen", True, raising=False)
    monkeypatch.setattr(register.sys, "executable", r"C:\Program Files\Shorekeep\Shorekeep.exe")

    line = register.linux_exec_line()

    assert line.startswith('"C:\\Program Files\\Shorekeep\\Shorekeep.exe"')
    assert line.endswith("%u")


def test_macos_url_types_declares_scheme():
    types = register.macos_url_types()

    assert types[0]["CFBundleURLSchemes"] == ["shorekeep"]
    assert types[0]["CFBundleURLName"] == "com.shorekeep.desktop"
