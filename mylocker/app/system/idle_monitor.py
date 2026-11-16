"""
idle_monitor.py — Detect system idle time via GetLastInputInfo.
Triggers auto-lock after N minutes of inactivity.
"""

from __future__ import annotations

import ctypes
import logging
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal

logger = logging.getLogger(__name__)


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("dwTime", ctypes.c_uint),
    ]


def get_idle_time_seconds() -> float:
    """Return seconds since last user input (keyboard/mouse)."""
    try:
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
            return max(0.0, millis / 1000.0)
    except Exception:
        pass
    return 0.0


class IdleMonitor(QObject):
    """Monitors idle time and emits idle_timeout signal."""

    idle_timeout = Signal()

    def __init__(
        self,
        idle_minutes: float = 5.0,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._idle_seconds = idle_minutes * 60
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._check)
        self._enabled = False

    def start(self) -> None:
        self._enabled = True
        self._timer.start(5000)  # check every 5 seconds

    def stop(self) -> None:
        self._enabled = False
        self._timer.stop()

    def set_idle_minutes(self, minutes: float) -> None:
        self._idle_seconds = minutes * 60

    def _check(self) -> None:
        if not self._enabled:
            return
        idle = get_idle_time_seconds()
        if idle >= self._idle_seconds:
            logger.info("Idle timeout reached (%.0fs idle).", idle)
            self.idle_timeout.emit()


__all__ = ["IdleMonitor", "get_idle_time_seconds"]

