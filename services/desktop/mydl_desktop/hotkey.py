from __future__ import annotations

import ctypes
import logging
import os
import threading
from typing import Callable

logger = logging.getLogger(__name__)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
HOTKEY_ID = 0x5A0A

SPECIAL_KEYS = {
    "space": 0x20,
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "esc": 0x1B,
    "escape": 0x1B,
    "backspace": 0x08,
    "delete": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "printscreen": 0x2C,
    "pause": 0x13,
}

MODIFIERS = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "option": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "super": MOD_WIN,
    "meta": MOD_WIN,
}


def _key_code(token: str) -> int | None:
    if token in SPECIAL_KEYS:
        return SPECIAL_KEYS[token]
    if len(token) == 1 and token.isalnum():
        return ord(token.upper())
    if token.startswith("f") and token[1:].isdigit():
        number = int(token[1:])
        if 1 <= number <= 24:
            return 0x70 + number - 1
    return None


def parse_hotkey(spec: str) -> tuple[int, int] | None:
    """Parse something like ctrl+alt+s into (modifiers, virtual key)."""
    tokens = [token.strip().lower() for token in spec.split("+") if token.strip()]
    if not tokens:
        return None
    modifiers = 0
    key = None
    for token in tokens:
        if token in MODIFIERS:
            modifiers |= MODIFIERS[token]
            continue
        code = _key_code(token)
        if code is None or key is not None:
            return None
        key = code
    if key is None or modifiers == 0:
        return None
    return modifiers, key


class HotkeyListener:
    """Windows global hotkey on its own message loop."""

    def __init__(self, spec: str, callback: Callable[[], None]) -> None:
        self.spec = spec
        self.callback = callback
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._registered = False

    def start(self) -> bool:
        if os.name != "nt":
            logger.info("global hotkeys are only available on Windows; %s is disabled", self.spec)
            return False
        if self.spec.strip().lower() in {"", "off", "none"}:
            return False
        if parse_hotkey(self.spec) is None:
            logger.warning("could not parse the global hotkey %r", self.spec)
            return False
        self._thread = threading.Thread(target=self._run, name="global-hotkey", daemon=True)
        self._thread.start()
        self._ready.wait(2.0)
        return self._registered

    def _run(self) -> None:
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()
        parsed = parse_hotkey(self.spec)
        if parsed is None:
            self._ready.set()
            return
        modifiers, key = parsed
        if not user32.RegisterHotKey(None, HOTKEY_ID, modifiers | MOD_NOREPEAT, key):
            logger.warning("the global hotkey %s is already in use by another app", self.spec)
            self._ready.set()
            return
        self._registered = True
        self._ready.set()
        try:
            message = wintypes.MSG()
            while not self._stop.is_set():
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result <= 0:
                    break
                if message.message == WM_HOTKEY and message.wParam == HOTKEY_ID:
                    try:
                        self.callback()
                    except Exception:
                        logger.exception("global hotkey callback failed")
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)

    def stop(self) -> None:
        self._stop.set()
        thread_id = self._thread_id
        if thread_id:
            ctypes.windll.user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout=2.0)
