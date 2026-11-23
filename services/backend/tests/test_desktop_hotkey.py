import pytest

pytest.importorskip("mydl_desktop")

from mydl_desktop.hotkey import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    MOD_WIN,
    HotkeyListener,
    parse_hotkey,
)


def test_parse_ctrl_alt_letter():
    assert parse_hotkey("ctrl+alt+s") == (MOD_CONTROL | MOD_ALT, ord("S"))


def test_parse_is_case_insensitive_and_ignores_spaces():
    assert parse_hotkey("  Ctrl + Shift + F9 ") == (MOD_CONTROL | MOD_SHIFT, 0x70 + 8)


def test_parse_aliases():
    assert parse_hotkey("option+space") == (MOD_ALT, 0x20)
    assert parse_hotkey("super+d") == (MOD_WIN, ord("D"))
    assert parse_hotkey("meta+return") == (MOD_WIN, 0x0D)


def test_parse_digit_and_function_keys():
    assert parse_hotkey("ctrl+7") == (MOD_CONTROL, ord("7"))
    assert parse_hotkey("alt+f24") == (MOD_ALT, 0x70 + 23)


@pytest.mark.parametrize(
    "spec",
    ["", "   ", "ctrl", "s", "ctrl+alt", "ctrl+alt+ss", "ctrl+alt+unknown", "alt+f25"],
)
def test_parse_rejects_incomplete_or_unknown_specs(spec):
    assert parse_hotkey(spec) is None


def test_listener_does_not_start_for_off_or_invalid_specs():
    calls: list = []
    assert HotkeyListener("off", lambda: calls.append(1)).start() is False
    assert HotkeyListener("not a hotkey", lambda: calls.append(1)).start() is False
    assert calls == []
