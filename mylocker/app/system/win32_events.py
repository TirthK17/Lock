"""
win32_events.py — Listen for Windows session lock and power suspension events.

Audit Requirements (Phase 4, Item 30):
  - Lock on Windows session lock (Win+L / WTS_SESSION_LOCK)
  - Lock on sleep / suspend (WM_POWERBROADCAST / PBT_APMSUSPEND)
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from typing import Optional

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication
from PySide6.QtWidgets import QWidget

from .lock_controller import get_lock_controller

logger = logging.getLogger(__name__)

# Windows message constants
WM_WTSSESSION_CHANGE = 0x02B1
WTS_SESSION_LOCK = 0x0007
WTS_SESSION_LOGOFF = 0x0005

WM_POWERBROADCAST = 0x0218
PBT_APMSUSPEND = 0x0004
PBT_APMSTANDBY = 0x0005
PBT_POWERSETTINGCHANGE = 0x8013

NOTIFY_FOR_THIS_SESSION = 0


class WindowsSecurityEventFilter(QAbstractNativeEventFilter):
    """Native event filter for session lock and system suspend."""

    def __init__(self) -> None:
        super().__init__()
        self._registered_hwnds: set[int] = set()

    def register_window(self, widget: QWidget) -> None:
        """Register a window handle with wtsapi32 for session notifications."""
        try:
            hwnd = int(widget.winId())
            if hwnd and hwnd not in self._registered_hwnds:
                res = ctypes.windll.wtsapi32.WTSRegisterSessionNotification(hwnd, NOTIFY_FOR_THIS_SESSION)
                if res:
                    self._registered_hwnds.add(hwnd)
                    logger.debug("Registered WTSRegisterSessionNotification for HWND %s", hwnd)
                else:
                    logger.warning("WTSRegisterSessionNotification failed for HWND %s", hwnd)
        except Exception as exc:
            logger.debug("Could not register session notification: %s", exc)

    def unregister_window(self, widget: QWidget) -> None:
        """Unregister window from session notifications."""
        try:
            hwnd = int(widget.winId())
            if hwnd in self._registered_hwnds:
                ctypes.windll.wtsapi32.WTSUnRegisterSessionNotification(hwnd)
                self._registered_hwnds.discard(hwnd)
        except Exception:
            pass

    def nativeEventFilter(self, event_type: bytes, message: int) -> tuple[bool, int]:
        """Inspect native Windows messages."""
        try:
            if event_type in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
                msg = wintypes.MSG.from_address(int(message))

                # Check Windows session lock (Win+L)
                if msg.message == WM_WTSSESSION_CHANGE:
                    if msg.wParam in (WTS_SESSION_LOCK, WTS_SESSION_LOGOFF):
                        logger.warning("Windows session lock/logoff detected — locking vault.")
                        get_lock_controller().lock("session_lock")

                # Check Sleep / Suspend / Standby
                elif msg.message == WM_POWERBROADCAST:
                    if msg.wParam in (PBT_APMSUSPEND, PBT_APMSTANDBY):
                        logger.warning("System sleep/suspend detected — locking vault.")
                        get_lock_controller().lock("suspend")

        except Exception as exc:
            logger.debug("Error in nativeEventFilter: %s", exc)

        return False, 0


_filter_instance: Optional[WindowsSecurityEventFilter] = None


def install_windows_security_filter() -> WindowsSecurityEventFilter:
    """Install the global Windows security native event filter."""
    global _filter_instance
    if _filter_instance is None:
        _filter_instance = WindowsSecurityEventFilter()
        app = QCoreApplication.instance()
        if app is not None:
            app.installNativeEventFilter(_filter_instance)
    return _filter_instance


__all__ = ["WindowsSecurityEventFilter", "install_windows_security_filter"]
