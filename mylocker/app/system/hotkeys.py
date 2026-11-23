"""
hotkeys.py — Global Panic Hotkey handler using Windows RegisterHotKey API.

Default hotkey: Ctrl + Alt + Shift + L
Instantly locks the vault, closes active viewers, and hides the application.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)

# Windows hotkey constants
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
HOTKEY_ID_PANIC = 9901


class GlobalHotkeyThread(threading.Thread):
    """Background message loop listening for global Windows hotkey."""

    def __init__(
        self,
        callback: Callable[[], None],
        on_registration_failed: Optional[Callable[[int], None]] = None,
        key_code: int = ord('L'),
        modifiers: int = (MOD_CONTROL | MOD_ALT | MOD_SHIFT),
    ) -> None:
        super().__init__(daemon=True, name="GlobalHotkeyThread")
        self.callback = callback
        self.on_registration_failed = on_registration_failed
        self.key_code = key_code
        self.modifiers = modifiers
        self.running = False
        self.thread_id: Optional[int] = None
        self.is_registered: bool = False

    def run(self) -> None:
        self.running = True
        self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()

        user32 = ctypes.windll.user32
        success = user32.RegisterHotKey(
            None,
            HOTKEY_ID_PANIC,
            self.modifiers | MOD_NOREPEAT,
            self.key_code,
        )

        if not success:
            err = ctypes.windll.kernel32.GetLastError()
            logger.warning("Failed to register global panic hotkey (GetLastError=%d)", err)
            if self.on_registration_failed:
                try:
                    self.on_registration_failed(err)
                except Exception:
                    pass
            return

        self.is_registered = True
        logger.info("Global panic hotkey registered (Ctrl+Alt+Shift+L).")

        msg = wintypes.MSG()
        while self.running:
            res = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if res <= 0:
                break
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID_PANIC:
                logger.warning("PANIC HOTKEY TRIGGERED!")
                try:
                    self.callback()
                except Exception as exc:
                    logger.error("Error in panic hotkey callback: %s", exc)

            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        user32.UnregisterHotKey(None, HOTKEY_ID_PANIC)
        logger.info("Global panic hotkey unregistered.")

    def stop(self) -> None:
        self.running = False
        if self.thread_id:
            # Post WM_QUIT to break GetMessage loop
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)


class PanicHotkeyManager(QObject):
    """Qt-friendly wrapper emitting a signal when the panic hotkey is pressed."""

    panic_triggered = Signal()
    registration_failed = Signal(int, str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._thread: Optional[GlobalHotkeyThread] = None
        self._last_error: Optional[int] = None

    @property
    def is_registered(self) -> bool:
        return self._thread is not None and self._thread.is_registered

    @property
    def last_error(self) -> Optional[int]:
        return self._last_error

    def _on_failed(self, error_code: int) -> None:
        self._last_error = error_code
        msg = (
            f"Global panic hotkey (Ctrl+Alt+Shift+L) could not be registered (error {error_code}). "
            "It may already be in use by another application."
        )
        self.registration_failed.emit(error_code, msg)

    def start(self) -> None:
        if self._thread is None:
            self._thread = GlobalHotkeyThread(
                callback=self.panic_triggered.emit,
                on_registration_failed=self._on_failed,
            )
            self._thread.start()

    def stop(self) -> None:
        if self._thread is not None:
            self._thread.stop()
            self._thread = None


__all__ = ["PanicHotkeyManager", "GlobalHotkeyThread"]

