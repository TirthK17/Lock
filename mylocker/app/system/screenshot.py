"""
screenshot.py — Windows screenshot and screen recording protection.

Uses Win32 SetWindowDisplayAffinity with WDA_EXCLUDEFROMCAPTURE (0x00000011)
or WDA_MONITOR (0x00000001) to prevent the window content from being captured
by screenshot tools, screen recorders, or remote desktop snooping.
"""

from __future__ import annotations

import ctypes
import logging
import os

from PySide6.QtWidgets import QWidget

logger = logging.getLogger(__name__)

WDA_NONE = 0x00000000
WDA_MONITOR = 0x00000001
WDA_EXCLUDEFROMCAPTURE = 0x00000011


def apply_screenshot_protection(widget: QWidget, enable: bool = True) -> bool:
    """Apply or remove screenshot/recording exclusion for a top-level widget.

    Args:
        widget: The QWidget / QDialog / QMainWindow to protect.
        enable: True to block capture, False to allow.

    Returns:
        True if the Win32 call succeeded, False otherwise.
    """
    try:
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return True

        hwnd = int(widget.winId())
        if not hwnd:
            return False

        if hasattr(ctypes.windll, "user32") and not ctypes.windll.user32.IsWindow(hwnd):
            return False

        affinity = WDA_EXCLUDEFROMCAPTURE if enable else WDA_NONE
        res = ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, affinity)
        if not res and enable:
            # Fall back to WDA_MONITOR if EXCLUDEFROMCAPTURE is not supported
            res = ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_MONITOR)

        if res:
            logger.debug("Screenshot protection set to %s for HWND %s", enable, hwnd)
            return True
        else:
            err = ctypes.windll.kernel32.GetLastError()
            logger.warning("SetWindowDisplayAffinity failed with error %d", err)
            return False
    except Exception as exc:
        logger.warning("Screenshot protection failed to apply: %s", exc)
        return False


__all__ = ["apply_screenshot_protection", "WDA_EXCLUDEFROMCAPTURE", "WDA_MONITOR", "WDA_NONE"]
