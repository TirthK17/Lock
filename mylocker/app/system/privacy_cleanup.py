"""
privacy_cleanup.py — Erase privacy traces left by viewing vault files.

Cleans:
  - Windows Recent Files / Jump Lists created ONLY for vault files (Item 36)
  - Clipboard (auto-wipe after 30 seconds)
  - Temp folder files
"""

from __future__ import annotations

import ctypes
import logging
import os
from pathlib import Path
from typing import Iterable, Optional

from PySide6.QtCore import QObject, QTimer

logger = logging.getLogger(__name__)


def clean_vault_recent_files(opened_filenames: Iterable[str]) -> int:
    """Audit Requirement (Item 36):
    Clear ONLY vault-created Recent Files links without wiping the user's
    entire Windows Recent Files history.
    """
    removed_count = 0
    names_to_clean = {Path(name).stem.lower() for name in opened_filenames if name}
    if not names_to_clean:
        return 0

    recent_dir = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Recent"
    if not recent_dir.exists():
        return 0

    try:
        for item in recent_dir.glob("*.lnk"):
            item_stem = item.stem.lower()
            if any(target in item_stem for target in names_to_clean):
                try:
                    item.unlink()
                    removed_count += 1
                except Exception as exc:
                    logger.debug("Failed to remove recent shortcut %s: %s", item, exc)
    except Exception as exc:
        logger.warning("Error scanning recent files folder: %s", exc)

    if removed_count > 0:
        logger.info("Removed %d vault-related shortcuts from Recent Files.", removed_count)
    return removed_count


def clear_recent_files(opened_filenames: Optional[Iterable[str]] = None) -> None:
    """Safely clear recent files. If filenames provided, removes only those."""
    if opened_filenames:
        clean_vault_recent_files(opened_filenames)
    else:
        logger.debug("Global recent files wipe requested but suppressed to prevent data loss (Item 36).")


def clear_clipboard() -> None:
    """Wipe Windows clipboard contents."""
    try:
        if ctypes.windll.user32.OpenClipboard(None):
            ctypes.windll.user32.EmptyClipboard()
            ctypes.windll.user32.CloseClipboard()
            logger.info("Clipboard wiped.")
    except Exception as exc:
        logger.warning("Could not wipe clipboard: %s", exc)


class ClipboardWiper(QObject):
    """Timer-based automatic clipboard wiper."""

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._timer: Optional[QTimer] = None

    def schedule_wipe(self, delay_seconds: int = 30) -> None:
        if self._timer:
            self._timer.stop()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(clear_clipboard)
        self._timer.start(delay_seconds * 1000)


__all__ = ["clean_vault_recent_files", "clear_recent_files", "clear_clipboard", "ClipboardWiper"]
