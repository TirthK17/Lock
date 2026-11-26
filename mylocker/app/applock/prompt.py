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

import logging
from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.keystore import Keystore
from ..core.lockout import LockoutManager
from ..core.settings_store import SettingsStore
from ..ui import icons
from ..ui.components import DangerButton, PrimaryButton, _apply_dialog_shadow
from ..ui.design_system import Tokens
from ..ui.widgets import FadeLabel
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
        lockout: Optional[LockoutManager] = None,
        settings_store: Optional[SettingsStore] = None,
        keystore: Optional[Keystore] = None,
        on_lockout_triggered: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(parent)
        self.pid = pid
        self.exe_name = exe_name
        self.verify_password_fn = verify_password_fn
        self.lockout = lockout
        self.settings_store = settings_store
        self.keystore = keystore
        self.on_lockout_triggered = on_lockout_triggered
        self.attempts = 0
        self.max_attempts = 3
        self.resolved = False
        self._photo_threads: list = []

        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowState(Qt.WindowState.WindowFullScreen)

        self._build_ui()
        self._apply_screenshot_protection()
        self._check_initial_lockout()

    def _apply_screenshot_protection(self) -> None:
        try:
            from ..system.screenshot import apply_screenshot_protection
            apply_screenshot_protection(self, True)
        except Exception:
            pass

    def _build_ui(self) -> None:
        p = Tokens.colors
        # Fullscreen dimmed background container
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        dimmer = QWidget(self)
        dimmer.setObjectName("AppLockDimmer")
        dimmer.setStyleSheet(f"#AppLockDimmer {{ background-color: {p.overlay}; }}")
        outer.addWidget(dimmer)

        dimmer_layout = QVBoxLayout(dimmer)
        dimmer_layout.addStretch(1)

        # Centered Card (400px, 16px dialog radius, hairline border, single soft shadow)
        card = QFrame(dimmer)
        card.setObjectName("AppLockPromptCard")
        card.setFixedWidth(400)
        card.setStyleSheet(f"""
            #AppLockPromptCard {{
                background-color: {p.card};
                border: 1px solid {p.border_strong};
                border-radius: {Tokens.radius.DIALOG}px;
            }}
        """)
        _apply_dialog_shadow(card)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(32, 32, 32, 32)
        card_layout.setSpacing(16)

        # SVG Shield Lock Icon
        icon_lbl = QLabel(card)
        icon_lbl.setFixedSize(48, 48)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setStyleSheet(
            f"background-color: {p.accent_tint}; border: 1px solid {p.accent_border}; border-radius: 12px;"
        )
        icon_lbl.setPixmap(icons.get_pixmap("shield_lock", size=24, color=p.accent))
        card_layout.addWidget(icon_lbl, 0, Qt.AlignmentFlag.AlignCenter)

        # Title
        title_lbl = QLabel("Application Locked", card)
        title_lbl.setFont(Tokens.type.font(Tokens.type.PAGE_TITLE, Tokens.type.WEIGHT_TITLE))
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_lbl.setStyleSheet(f"color: {p.text}; background: transparent;")
        card_layout.addWidget(title_lbl)

        # Subtitle
        sub_lbl = QLabel(f"Enter master password to launch {self.exe_name}", card)
        sub_lbl.setFont(Tokens.type.font(Tokens.type.BODY, Tokens.type.WEIGHT_BODY))
        sub_lbl.setWordWrap(True)
        sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub_lbl.setStyleSheet(f"color: {p.text_secondary}; background: transparent;")
        card_layout.addWidget(sub_lbl)

        # Password input
        self.pw_input = QLineEdit(card)
        self.pw_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.pw_input.setPlaceholderText("Master or App Lock password…")
        self.pw_input.setAccessibleName("App Lock password")
        self.pw_input.setFixedHeight(44)
        self.pw_input.returnPressed.connect(self._verify)
        card_layout.addWidget(self.pw_input)

        # Error label
        self.error_lbl = FadeLabel("")
        self.error_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.error_lbl.setStyleSheet("background: transparent;")
        card_layout.addWidget(self.error_lbl)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        cancel_btn = DangerButton("Block && Terminate", parent=card)
        cancel_btn.setFixedHeight(40)
        cancel_btn.clicked.connect(self._terminate)
        btn_layout.addWidget(cancel_btn, 1)

        unlock_btn = PrimaryButton("Unlock App", parent=card)
        unlock_btn.setFixedHeight(40)
        unlock_btn.clicked.connect(self._verify)
        btn_layout.addWidget(unlock_btn, 1)

        card_layout.addLayout(btn_layout)

        h = QHBoxLayout()
        h.addStretch(1)
        h.addWidget(card)
        h.addStretch(1)

        dimmer_layout.addLayout(h)
        dimmer_layout.addStretch(1)

        # Auto focus
        QTimer.singleShot(100, self.pw_input.setFocus)

    def _check_initial_lockout(self) -> None:
        if not self.lockout:
            return
        try:
            self.lockout.check_lockout()
        except Exception:
            remaining = self.lockout.get_lockout_seconds()
            self.error_lbl.show_text(f"Too many failed attempts. Wait {remaining:.0f}s.", Tokens.colors.danger)
            self.pw_input.setEnabled(False)
            if self.on_lockout_triggered:
                try:
                    self.on_lockout_triggered()
                except Exception:
                    pass
            QTimer.singleShot(1500, self._terminate)

    def _capture_intruder_photo(self) -> None:
        if not self.lockout:
            return
        try:
            from ..system.webcam import IntruderPhotoCaptureThread
            thread = IntruderPhotoCaptureThread(self.lockout.vault_dir, self.lockout, parent=self)
            self._photo_threads.append(thread)
            thread.finished.connect(
                lambda: self._photo_threads.remove(thread) if thread in self._photo_threads else None
            )
            thread.start()
        except Exception as exc:
            logger.warning("Failed to start background intruder capture thread: %s", exc)

    def _verify(self) -> None:
        entered = self.pw_input.text()
        if not entered:
            return

        if self.lockout:
            try:
                self.lockout.check_lockout()
            except Exception:
                remaining = self.lockout.get_lockout_seconds()
                self.error_lbl.show_text(
                    f"Too many failed attempts. Wait {remaining:.0f}s. Terminating app…",
                    Tokens.colors.danger,
                )
                self.pw_input.setEnabled(False)
                if self.on_lockout_triggered:
                    try:
                        self.on_lockout_triggered()
                    except Exception:
                        pass
                QTimer.singleShot(1500, self._terminate)
                return

        if self.verify_password_fn(entered):
            if self.lockout:
                try:
                    self.lockout.record_success()
                except Exception:
                    pass
            self.resolved = True
            resume_process(self.pid)
            self.unlocked.emit(self.pid, self.exe_name)
            self.accept()
        else:
            self.attempts += 1
            count = 0
            remaining = 0.0
            if self.lockout:
                try:
                    count = self.lockout.record_failure()
                    remaining = self.lockout.get_lockout_seconds()
                except Exception as exc:
                    logger.warning("LockoutManager failure recording error: %s", exc)

                # Capture webcam photo in background thread on failed attempt
                self._capture_intruder_photo()

            # Emergency self-destruct if enabled and 20 failed attempts reached
            if (
                self.settings_store
                and self.settings_store.get("self_destruct_enabled", False)
                and count >= 20
                and self.keystore
            ):
                try:
                    self.keystore.self_destruct()
                    self.error_lbl.show_text(
                        "Vault self-destruct activated after 20 failed attempts.",
                        Tokens.colors.danger,
                    )
                    self.pw_input.setEnabled(False)
                    if self.on_lockout_triggered:
                        try:
                            self.on_lockout_triggered()
                        except Exception:
                            pass
                    QTimer.singleShot(1500, self._terminate)
                    return
                except Exception:
                    pass

            if remaining > 0:
                self.error_lbl.show_text(
                    f"Too many failed attempts. Wait {remaining:.0f}s. Terminating app…",
                    Tokens.colors.danger,
                )
                self.pw_input.setEnabled(False)
                if self.on_lockout_triggered:
                    try:
                        self.on_lockout_triggered()
                    except Exception:
                        pass
                QTimer.singleShot(1500, self._terminate)
            else:
                rem_local = self.max_attempts - self.attempts
                if rem_local <= 0:
                    self.error_lbl.show_text("Too many failed attempts. Terminating app…", Tokens.colors.danger)
                    self.pw_input.setEnabled(False)
                    QTimer.singleShot(1200, self._terminate)
                else:
                    self.error_lbl.show_text(f"Incorrect password ({rem_local} attempts remaining)", Tokens.colors.danger)
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
        for th in list(self._photo_threads):
            if th.isRunning():
                th.wait(2000)
        self._photo_threads.clear()
        if not self.resolved:
            terminate_process(self.pid)
            self.cancelled.emit(self.pid, self.exe_name)
        super().closeEvent(event)


__all__ = ["AppLockPrompt"]

