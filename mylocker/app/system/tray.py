"""
tray.py — System tray icon with pystray + PySide6 integration.

Features:
  - Tray icon reflects vault status (locked 🔒 vs unlocked 🔓)
  - Right-click menu: Open, Lock, Quick Panic, Exit
  - Double-click to show/hide window
  - Runs in background when window is minimized
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

logger = logging.getLogger(__name__)


def _make_tray_icon(locked: bool) -> QIcon:
    """Generate a programmatic tray icon (no file dependency)."""
    px = QPixmap(32, 32)
    px.fill(QColor(0, 0, 0, 0))
    p = QPainter(px)
    p.setFont(QFont("Segoe UI Emoji", 20))
    p.drawText(0, 26, "🔒" if locked else "🔓")
    p.end()
    return QIcon(px)


class VaultTrayIcon(QSystemTrayIcon):
    """System tray icon for Personal Vault."""

    open_requested = Signal()
    lock_requested = Signal()
    panic_requested = Signal()
    exit_requested = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._is_locked = True
        self.setIcon(_make_tray_icon(locked=True))
        self.setToolTip("Personal Vault — Locked")

        # Build context menu
        menu = QMenu()
        self._open_act = menu.addAction("🔓 Open Vault", self.open_requested.emit)
        self._lock_act = menu.addAction("🔒 Lock Now", self.lock_requested.emit)
        menu.addSeparator()
        menu.addAction("🚨 Panic (Lock & Hide)", self.panic_requested.emit)
        menu.addSeparator()
        menu.addAction("Exit", self.exit_requested.emit)

        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)

    def set_locked(self, locked: bool) -> None:
        self._is_locked = locked
        self.setIcon(_make_tray_icon(locked))
        self.setToolTip("Personal Vault — " + ("Locked" if locked else "Unlocked"))
        self._open_act.setEnabled(locked)
        self._lock_act.setEnabled(not locked)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.open_requested.emit()


__all__ = ["VaultTrayIcon"]

