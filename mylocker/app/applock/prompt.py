"""
prompt.py — Fullscreen topmost password overlay prompt when an App-Locked
application attempts to start.

Security details:
  - TopMost and FullScreen to prevent casual bypass.
  - SetWindowDisplayAffinity to prevent screen recording / screenshots.
  - If password is correct, calls resume_process().
  - If user cancels, closes, or fails max attempts, terminates the process.
"""

from __future__ import annotations

import ctypes
import logging
from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..ui.theme import Colors
from ..ui.widgets import FadeLabel, GlowButton
from .process_utils import resume_process, terminate_process

logger = logging.getLogger(__name__)

WDA_EXCLUDEFROMCAPTURE = 0x11


class AppLockPrompt(QDialog):
    """Fullscreen topmost modal dialog requiring authentication before launching a locked app."""

    unlocked = Signal(int, str)  # pid, exe_name
    cancelled = Signal(int, str)  # pid, exe_name

    def __init__(
        self,
        pid: int,
        exe_name: str,
        verify_password_fn: Callable[[str], bool],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.pid = pid
        self.exe_name = exe_name
        self.verify_password_fn = verify_password_fn
        self.attempts = 0
        self.max_attempts = 3
        self.resolved = False

        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowState(Qt.WindowState.WindowFullScreen)

        self._build_ui()
        self._apply_screenshot_protection()

    def _apply_screenshot_protection(self) -> None:
        try:
            hwnd = int(self.winId())
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
        except Exception:
            pass

    def _build_ui(self) -> None:
        # Fullscreen dimmed background container
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        dimmer = QWidget(self)
        dimmer.setStyleSheet("background-color: rgba(5, 7, 12, 0.85);")
        outer.addWidget(dimmer)

        dimmer_layout = QVBoxLayout(dimmer)
        dimmer_layout.addStretch(1)

        # Centered Card
        card = QFrame()
        card.setObjectName("AppLockPromptCard")
        card.setFixedWidth(420)
        card.setStyleSheet(f"""
            #AppLockPromptCard {{
                background-color: {Colors.BG_CARD};
                border: 1px solid {Colors.BORDER};
                border-radius: 20px;
            }}
        """)
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setColor(QColor(0, 0, 0, 180))
        shadow.setBlurRadius(50)
        shadow.setOffset(0, 15)
        card.setGraphicsEffect(shadow)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(36, 36, 36, 36)
        card_layout.setSpacing(16)

        # Lock Icon
        icon_lbl = QLabel("🛡️")
        icon_lbl.setFont(QFont("Segoe UI Emoji", 42))
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setStyleSheet("background: transparent;")
        card_layout.addWidget(icon_lbl)

        # Title
        title_lbl = QLabel("Application Locked")
        title_lbl.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_lbl.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; background: transparent;")
        card_layout.addWidget(title_lbl)

        # Subtitle
        sub_lbl = QLabel(f"Enter master password to unlock:\n{self.exe_name}")
        sub_lbl.setFont(QFont("Segoe UI", 12))
        sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub_lbl.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; background: transparent;")
        card_layout.addWidget(sub_lbl)
        card_layout.addSpacing(8)

        # Password input
        self.pw_input = QLineEdit()
        self.pw_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.pw_input.setPlaceholderText("Password…")
        self.pw_input.setFixedHeight(44)
        self.pw_input.returnPressed.connect(self._verify)
        card_layout.addWidget(self.pw_input)

        # Error label
        self.error_lbl = FadeLabel("")
        self.error_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.error_lbl)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        cancel_btn = QPushButton("Block & Terminate")
        cancel_btn.setProperty("danger", True)
        cancel_btn.setFixedHeight(42)
        cancel_btn.clicked.connect(self._terminate)
        btn_layout.addWidget(cancel_btn)

        unlock_btn = GlowButton("Unlock App")
        unlock_btn.setFixedHeight(42)
        unlock_btn.clicked.connect(self._verify)
        btn_layout.addWidget(unlock_btn)

        card_layout.addLayout(btn_layout)

        h = QHBoxLayout()
        h.addStretch(1)
        h.addWidget(card)
        h.addStretch(1)

        dimmer_layout.addLayout(h)
        dimmer_layout.addStretch(1)

        # Auto focus
        QTimer.singleShot(100, self.pw_input.setFocus)

    def _verify(self) -> None:
        entered = self.pw_input.text()
        if not entered:
            return

        if self.verify_password_fn(entered):
            self.resolved = True
            resume_process(self.pid)
            self.unlocked.emit(self.pid, self.exe_name)
            self.accept()
        else:
            self.attempts += 1
            remaining = self.max_attempts - self.attempts
            if remaining <= 0:
                self.error_lbl.show_text("Too many failed attempts. Terminating app…", Colors.DANGER)
                self.pw_input.setEnabled(False)
                QTimer.singleShot(1200, self._terminate)
            else:
                self.error_lbl.show_text(f"Incorrect password ({remaining} attempts remaining)", Colors.DANGER)
                self.pw_input.clear()
                self.pw_input.setFocus()

    def _terminate(self) -> None:
        self.resolved = True
        terminate_process(self.pid)
        self.cancelled.emit(self.pid, self.exe_name)
        self.reject()

    def keyPressEvent(self, event):
        # Prevent escaping without terminating
        if event.key() == Qt.Key.Key_Escape:
            self._terminate()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        if not self.resolved:
            terminate_process(self.pid)
            self.cancelled.emit(self.pid, self.exe_name)
        super().closeEvent(event)


__all__ = ["AppLockPrompt"]

