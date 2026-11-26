"""
login.py — Personal Vault Login Screen & Recovery Dialogs.

Redesigned with the Midnight Black + Deep Navy + Ice Blue + Electric Cyan visual
system (see `login_theme.py`), while preserving every security invariant and API:
  - Zero sensitive metadata exposed until authenticated.
  - Background QThread verification (LoginWorker) prevents GUI freezes during Argon2id.
  - Password visibility toggle inside frosted dark container.
  - Key-file selector with Browse, state feedback, and file readability checks.
  - Immediate validation prevents empty inputs / missing keyfile from burning lockout.
  - Non-flickering live countdown timer during active lockout.
  - Recovery Key / Forgot Password modal flows preserved unchanged in behavior.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

from PySide6.QtCore import (
    QObject,
    QSize,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QFont,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core.password_policy import validate_password
from . import icons
from .components import DialogShell, PrimaryButton, SecondaryButton
from .design_system import Tokens
from .login_theme import (
    AmbientLoginBackdrop,
    DividerWithSparkle,
    KeyFileSelector,
    LoginCard,
    LoginColors,
    LoginTypography,
    PasswordInputField,
    SecurityStatusBadge,
    ShieldLockEmblem,
    UnlockPrimaryButton,
    is_reduced_motion,
)
from .widgets import PasswordStrengthBar

logger = logging.getLogger(__name__)


def _is_auth_failure(exc: Exception) -> bool:
    """Return True if exception indicates incorrect credentials, False for system/IO/config errors."""
    from ..core.crypto import CryptoError
    if isinstance(exc, (OSError, FileNotFoundError, PermissionError, ValueError)):
        return False
    return isinstance(exc, CryptoError)


# ─── Background Login Worker ─────────────────────────────────────────────────


class LoginWorker(QThread):
    """Background worker thread to perform Argon2id KDF derivation and key unwrapping.

    Keeps the main GUI thread 100% responsive, preventing window freezes or
    '(Not Responding)' OS dialogs during intense cryptographic calibration.
    """

    success = Signal(object, bool)      # (master_key: bytearray, is_decoy: bool)
    failure = Signal(str, int, float)   # (message: str, fail_count: int, remaining_secs: float)
    error = Signal(str)

    def __init__(
        self,
        keystore,
        lockout,
        password: str,
        keyfile_path: Optional[str] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.keystore = keystore
        self.lockout = lockout
        self.password = password
        self.keyfile_path = keyfile_path
        self.elapsed_s: float = 0.0

    def run(self) -> None:
        t0 = time.perf_counter()
        # Check active lockout before doing any expensive work
        if self.lockout:
            try:
                self.lockout.check_lockout()
            except Exception as exc:
                self.failure.emit(
                    str(exc),
                    self.lockout.get_fail_count(),
                    self.lockout.get_lockout_seconds(),
                )
                return

        if not self.keystore:
            self.error.emit("No vault configured.")
            return

        # 1. Attempt primary vault unlock
        primary_exc = None
        try:
            mk = self.keystore.unlock_with_password(self.password, keyfile_path=self.keyfile_path)
            self.elapsed_s = time.perf_counter() - t0
            if self.lockout:
                self.lockout.record_success()
            self.success.emit(mk, False)
            return
        except Exception as exc:
            primary_exc = exc

        # 2. Attempt decoy vault unlock (if configured)
        if hasattr(self.keystore, "config") and self.keystore.config.has_decoy:
            try:
                mk = self.keystore.unlock_decoy(self.password)
                self.elapsed_s = time.perf_counter() - t0
                if self.lockout:
                    self.lockout.record_success()
                self.success.emit(mk, True)
                return
            except Exception:
                pass

        self.elapsed_s = time.perf_counter() - t0
        # 3. Authentication failed — record in LockoutManager only if genuine auth error
        is_auth_err = primary_exc is None or _is_auth_failure(primary_exc)
        count = 0
        remaining = 0.0
        if is_auth_err and self.lockout:
            count = self.lockout.record_failure()
            remaining = self.lockout.get_lockout_seconds()
        elif self.lockout:
            remaining = self.lockout.get_lockout_seconds()

        if not is_auth_err and primary_exc:
            msg = f"Vault error: {primary_exc}"
        elif remaining > 0:
            msg = f"Too many failed attempts. Wait {remaining:.0f}s."
        else:
            suffix = f" ({count} failed attempt{'s' if count > 1 else ''})" if count > 0 else ""
            msg = f"Incorrect password. Please try again.{suffix}"

        self.failure.emit(msg, count, remaining)


# ─── Modern Recovery Key Dialog ──────────────────────────────────────────────


class RecoveryKeyDialog(DialogShell):
    """Modal DialogShell for entering the 256-bit Base32 emergency recovery key."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        self._recovery_key = ""
        super().__init__(
            title="Enter Recovery Key",
            subtitle="Enter the 256-bit Base32 emergency recovery key saved during setup. Dashes are optional.",
            icon_name="key",
            icon_color=Tokens.colors.accent,
            parent=parent,
            min_width=460,
            max_width=540,
        )
        self._setup_ui()

    def _setup_ui(self) -> None:
        p = Tokens.colors
        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self._key_input = QLineEdit(self.body_widget)
        self._key_input.setPlaceholderText("XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX…")
        self._key_input.setAccessibleName("Recovery key")
        self._key_input.setFont(QFont("Cascadia Code, Consolas, Courier New", 11))
        self._key_input.setFixedHeight(42)
        self._key_input.returnPressed.connect(self._on_submit)
        input_row.addWidget(self._key_input, 1)

        paste_btn = SecondaryButton("Paste", icon_name="copy", parent=self.body_widget)
        paste_btn.setFixedHeight(42)
        paste_btn.clicked.connect(self._paste_clipboard)
        input_row.addWidget(paste_btn)
        self.body_layout.addLayout(input_row)

        self._error_lbl = QLabel("", self.body_widget)
        self._error_lbl.setStyleSheet(f"color: {p.danger}; font-size: {Tokens.type.LABEL}px; background: transparent;")
        self._error_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body_layout.addWidget(self._error_lbl)

        cancel_btn = SecondaryButton("Cancel", parent=self)
        cancel_btn.clicked.connect(self.reject)
        self.add_footer_button(cancel_btn)

        self._submit_btn = PrimaryButton("Unlock with Key", parent=self)
        self._submit_btn.clicked.connect(self._on_submit)
        self.add_footer_button(self._submit_btn)

    def _paste_clipboard(self) -> None:
        text = QApplication.clipboard().text().strip()
        if text:
            self._key_input.setText(text)
            self._error_lbl.setText("")

    def _on_submit(self) -> None:
        key = self._key_input.text().strip().upper()
        if not key:
            self._error_lbl.setText("Please enter your recovery key.")
            return
        self._recovery_key = key
        self.accept()

    def get_recovery_key(self) -> str:
        return self._recovery_key


# ─── Forgot Password / Recovery Reset Dialog & Worker ─────────────────────────


class RecoveryWorker(QThread):
    """Runs Argon2id recovery verification & optional password reset off the UI thread."""

    success = Signal(object, bool)      # (master_key: bytearray, password_reset: bool)
    failure = Signal(str, int, float)   # (message, fail_count, remaining_secs)
    error = Signal(str)

    def __init__(
        self,
        keystore,
        lockout,
        recovery_key: str,
        new_password: Optional[str] = None,
        keyfile_path: Optional[str] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.keystore = keystore
        self.lockout = lockout
        self.recovery_key = recovery_key
        self.new_password = new_password
        self.keyfile_path = keyfile_path

    def run(self) -> None:
        if self.lockout:
            try:
                self.lockout.check_lockout()
            except Exception as exc:
                self.failure.emit(
                    str(exc),
                    self.lockout.get_fail_count(),
                    self.lockout.get_lockout_seconds(),
                )
                return

        if not self.keystore:
            self.error.emit("No vault configured.")
            return

        try:
            if self.new_password is not None:
                mk = self.keystore.reset_password_with_recovery_key(
                    self.recovery_key,
                    self.new_password,
                    keyfile_path=self.keyfile_path,
                )
                pw_reset = True
            else:
                mk = self.keystore.unlock_with_recovery_key(self.recovery_key)
                pw_reset = False
            if self.lockout:
                self.lockout.record_success()
            self.success.emit(mk, pw_reset)
            return
        except ValueError as exc:
            self.error.emit(str(exc))
            return
        except Exception:
            pass

        count = 0
        remaining = 0.0
        if self.lockout:
            count = self.lockout.record_failure()
            remaining = self.lockout.get_lockout_seconds()

        if remaining > 0:
            msg = f"Too many failed attempts. Wait {remaining:.0f}s."
        else:
            suffix = f" ({count} failed attempt{'s' if count > 1 else ''})" if count > 0 else ""
            msg = f"Invalid recovery key. Please check it and try again. [ERR-REC-01]{suffix}"
        self.failure.emit(msg, count, remaining)


class ForgotPasswordDialog(DialogShell):
    """Modal DialogShell to reset Master Password using the 256-bit Recovery Key (or unlock once)."""

    def __init__(
        self,
        keystore,
        lockout=None,
        parent: Optional[QWidget] = None,
    ) -> None:
        self._keystore = keystore
        self._lockout = lockout
        self._worker: Optional[RecoveryWorker] = None
        self._master_key: Optional[bytearray] = None
        self._password_reset: bool = False
        self._photo_threads: list[QThread] = []
        super().__init__(
            title="Recover Access & Reset Password",
            subtitle="Enter your 256-bit Base32 recovery key saved during setup to unlock your vault and set a new Master Password.",
            icon_name="key",
            icon_color=Tokens.colors.accent,
            parent=parent,
            min_width=480,
            max_width=560,
        )
        self._setup_ui()

    def _attach_eye_toggle(self, line_edit: QLineEdit) -> None:
        action = QAction(line_edit)
        action.setIcon(icons.get_icon("eye", color=Tokens.colors.text_muted))
        action.setToolTip("Show / Hide password")

        def _toggle() -> None:
            p = Tokens.colors
            if line_edit.echoMode() == QLineEdit.EchoMode.Password:
                line_edit.setEchoMode(QLineEdit.EchoMode.Normal)
                action.setIcon(icons.get_icon("eye_off", color=p.accent))
            else:
                line_edit.setEchoMode(QLineEdit.EchoMode.Password)
                action.setIcon(icons.get_icon("eye", color=p.text_muted))

        action.triggered.connect(_toggle)
        line_edit.addAction(action, QLineEdit.ActionPosition.TrailingPosition)

    def _setup_ui(self) -> None:
        p = Tokens.colors
        rec_lbl = QLabel("Recovery Key:", self.body_widget)
        rec_lbl.setFont(Tokens.type.font(Tokens.type.LABEL, Tokens.type.WEIGHT_LABEL))
        rec_lbl.setStyleSheet(f"color: {p.text_secondary}; background: transparent;")
        self.body_layout.addWidget(rec_lbl)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self._key_input = QLineEdit(self.body_widget)
        self._key_input.setPlaceholderText("XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX…")
        self._key_input.setAccessibleName("Recovery key")
        self._key_input.setFont(QFont("Cascadia Code, Consolas, Courier New", 11))
        self._key_input.setFixedHeight(40)
        input_row.addWidget(self._key_input, 1)

        paste_btn = SecondaryButton("Paste", icon_name="copy", parent=self.body_widget)
        paste_btn.setFixedHeight(40)
        paste_btn.clicked.connect(self._paste_clipboard)
        input_row.addWidget(paste_btn)
        self.body_layout.addLayout(input_row)

        self._reset_check = QCheckBox("Set a new Master Password (Recommended)", self.body_widget)
        self._reset_check.setChecked(True)
        self._reset_check.toggled.connect(self._on_mode_toggled)
        self.body_layout.addWidget(self._reset_check)

        self._pw_container = QWidget(self.body_widget)
        self._pw_container.setStyleSheet("background: transparent;")
        pw_lay = QVBoxLayout(self._pw_container)
        pw_lay.setContentsMargins(0, 4, 0, 0)
        pw_lay.setSpacing(8)

        self._new_pw1 = QLineEdit(self._pw_container)
        self._new_pw1.setEchoMode(QLineEdit.EchoMode.Password)
        self._new_pw1.setPlaceholderText("New Master Password (min 12 chars, mixed case + digit + symbol)")
        self._new_pw1.setFixedHeight(40)
        self._attach_eye_toggle(self._new_pw1)
        pw_lay.addWidget(self._new_pw1)

        self._strength = PasswordStrengthBar()
        pw_lay.addWidget(self._strength)
        self._new_pw1.textChanged.connect(self._strength.update_password)

        self._new_pw2 = QLineEdit(self._pw_container)
        self._new_pw2.setEchoMode(QLineEdit.EchoMode.Password)
        self._new_pw2.setPlaceholderText("Confirm new Master Password…")
        self._new_pw2.setFixedHeight(40)
        self._attach_eye_toggle(self._new_pw2)
        pw_lay.addWidget(self._new_pw2)

        kf_enabled_now = False
        try:
            if self._keystore and self._keystore.config.exists():
                self._keystore.config.load()
                kf_enabled_now = bool(getattr(self._keystore.config, "keyfile_enabled", False))
        except Exception:
            pass

        self._use_kf_check = QCheckBox("Require a USB key file with the new password", self._pw_container)
        self._use_kf_check.setChecked(kf_enabled_now)
        self._use_kf_check.toggled.connect(self._on_kf_toggled)
        pw_lay.addWidget(self._use_kf_check)

        self._kf_row_w = QWidget(self._pw_container)
        self._kf_row_w.setStyleSheet("background: transparent;")
        kf_row = QHBoxLayout(self._kf_row_w)
        kf_row.setContentsMargins(0, 0, 0, 0)
        kf_row.setSpacing(8)
        self._kf_edit = QLineEdit(self._kf_row_w)
        self._kf_edit.setPlaceholderText("Path to key file…")
        self._kf_edit.setFixedHeight(36)
        kf_browse = SecondaryButton("Browse…", parent=self._kf_row_w)
        kf_browse.setFixedHeight(36)
        kf_browse.clicked.connect(self._browse_keyfile)
        kf_row.addWidget(self._kf_edit, 1)
        kf_row.addWidget(kf_browse)
        pw_lay.addWidget(self._kf_row_w)
        self._kf_row_w.setVisible(kf_enabled_now)

        self.body_layout.addWidget(self._pw_container)

        self._new_pw1.returnPressed.connect(self._new_pw2.setFocus)
        self._new_pw2.returnPressed.connect(self._on_submit)
        self._key_input.returnPressed.connect(
            lambda: self._new_pw1.setFocus() if self._reset_check.isChecked() else self._on_submit()
        )

        self._error_lbl = QLabel("", self.body_widget)
        self._error_lbl.setStyleSheet(f"color: {p.danger}; font-size: {Tokens.type.LABEL}px; background: transparent;")
        self._error_lbl.setWordWrap(True)
        self._error_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body_layout.addWidget(self._error_lbl)

        self._cancel_btn = SecondaryButton("Cancel", parent=self)
        self._cancel_btn.clicked.connect(self.reject)
        self.add_footer_button(self._cancel_btn)

        self._submit_btn = PrimaryButton("Reset Password & Unlock", icon_name="unlock", parent=self)
        self._submit_btn.clicked.connect(self._on_submit)
        self.add_footer_button(self._submit_btn)

        self.adjustSize()

    def _on_mode_toggled(self, reset_mode: bool) -> None:
        self._pw_container.setVisible(reset_mode)
        self._submit_btn.setText("Reset Password & Unlock" if reset_mode else "Unlock with Key")
        self._error_lbl.setText("")
        self.adjustSize()

    def _on_kf_toggled(self, checked: bool) -> None:
        self._kf_row_w.setVisible(checked)
        self.adjustSize()

    def _browse_keyfile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select USB Key File")
        if path:
            self._kf_edit.setText(path)

    def _paste_clipboard(self) -> None:
        text = QApplication.clipboard().text().strip()
        if text:
            self._key_input.setText(text)
            self._error_lbl.setText("")

    def _set_busy(self, busy: bool) -> None:
        self._submit_btn.setEnabled(not busy)
        self._cancel_btn.setEnabled(not busy)
        self._key_input.setEnabled(not busy)
        self._new_pw1.setEnabled(not busy)
        self._new_pw2.setEnabled(not busy)
        self._reset_check.setEnabled(not busy)
        if busy:
            self._submit_btn.setText(
                "Verifying & resetting…" if self._reset_check.isChecked() else "Verifying recovery key…"
            )
        else:
            self._submit_btn.setText(
                "Reset Password & Unlock" if self._reset_check.isChecked() else "Unlock with Key"
            )

    def _on_submit(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return

        rec_key = self._key_input.text().strip().upper()
        if not rec_key:
            self._error_lbl.setText("Please enter your recovery key.")
            self._key_input.setFocus()
            return

        new_pw: Optional[str] = None
        kf_path: Optional[str] = None
        if self._reset_check.isChecked():
            p1 = self._new_pw1.text()
            p2 = self._new_pw2.text()
            ok, msg, _ = validate_password(p1)
            if not ok:
                self._error_lbl.setText(msg)
                self._new_pw1.setFocus()
                return
            if p1 != p2:
                self._error_lbl.setText("New passwords do not match.")
                self._new_pw2.setFocus()
                return
            if self._use_kf_check.isChecked():
                kf_text = self._kf_edit.text().strip()
                if not kf_text or not os.path.isfile(kf_text):
                    self._error_lbl.setText("Please select a valid USB key file or uncheck the key-file option.")
                    return
                kf_path = kf_text
            new_pw = p1

        if self._lockout:
            try:
                self._lockout.check_lockout()
            except Exception as exc:
                self._error_lbl.setText(str(exc))
                return

        self._error_lbl.setText("")
        self._set_busy(True)

        self._worker = RecoveryWorker(
            keystore=self._keystore,
            lockout=self._lockout,
            recovery_key=rec_key,
            new_password=new_pw,
            keyfile_path=kf_path,
            parent=self,
        )
        self._worker.success.connect(self._on_worker_success)
        self._worker.failure.connect(self._on_worker_failure)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

    def _on_worker_success(self, master_key: bytearray, password_reset: bool) -> None:
        if self._worker:
            self._worker.wait(2000)
        self._set_busy(False)
        self._master_key = master_key
        self._password_reset = password_reset
        self._new_pw1.clear()
        self._new_pw2.clear()
        self._key_input.clear()
        self.accept()

    def _on_worker_failure(self, msg: str, _count: int, _remaining: float) -> None:
        if self._worker:
            self._worker.wait(2000)
        self._set_busy(False)
        self._error_lbl.setText(msg)
        if self._lockout:
            QTimer.singleShot(50, self._capture_intruder_photo)

    def _capture_intruder_photo(self) -> None:
        if not self._lockout:
            return
        try:
            from ..system.webcam import IntruderPhotoCaptureThread
            thread = IntruderPhotoCaptureThread(self._lockout.vault_dir, self._lockout, parent=self)
            self._photo_threads.append(thread)
            thread.finished.connect(
                lambda: self._photo_threads.remove(thread) if thread in self._photo_threads else None
            )
            thread.start()
        except Exception as exc:
            logger.warning("Failed to start background intruder capture thread: %s", exc)

    def closeEvent(self, event) -> None:
        for th in list(self._photo_threads):
            try:
                if th.isRunning():
                    th.wait(1000)
            except Exception:
                pass
        self._photo_threads.clear()
        super().closeEvent(event)

    def _on_worker_error(self, msg: str) -> None:
        if self._worker:
            self._worker.wait(2000)
        self._set_busy(False)
        self._error_lbl.setText(msg)

    def get_result(self) -> tuple[Optional[bytearray], bool]:
        return self._master_key, self._password_reset


# ─── Login Screen ─────────────────────────────────────────────────────────────


class LoginScreen(QWidget):
    """Full-screen cinematic Login Screen recreating the Midnight/Ice-Blue design."""

    login_success = Signal(object, bool)      # (master_key: bytearray, is_decoy: bool)
    recovery_requested = Signal()
    switch_vault_requested = Signal()
    setup_requested = Signal()

    def __init__(
        self,
        keystore=None,
        lockout_manager=None,
        settings_store=None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._keystore = keystore
        self._lockout = lockout_manager
        self._settings = settings_store
        self._attempt_in_progress = False
        self._keyfile_required = False
        self._lockout_timer: Optional[QTimer] = None
        self._success_timer = QTimer(self)
        self._success_timer.setSingleShot(True)
        self._success_timer.timeout.connect(self._emit_pending_success)
        self._pending_success: Optional[tuple[object, bool]] = None
        self._worker: Optional[LoginWorker] = None
        self._photo_threads: list[QThread] = []
        self.last_unlock_seconds: float = 0.0

        self._setup_ui()

    def _is_reduced_motion(self) -> bool:
        return is_reduced_motion(self._settings)

    def _setup_ui(self) -> None:
        self.setObjectName("LoginScreen")
        self.setMinimumSize(460, 560)
        self.setStyleSheet(f"#LoginScreen {{ background: {LoginColors.BG_PRIMARY}; }}")

        reduced = self._is_reduced_motion()

        # Background painter fills behind centered scroll viewport
        self._backdrop = AmbientLoginBackdrop(reduced_motion=reduced, parent=self)
        self._backdrop.lower()

        # Root layout holds a transparent scroll area so short/high-DPI viewports never clip
        root_lay = QVBoxLayout(self)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }"
        )
        self._scroll.viewport().setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._scroll.viewport().setStyleSheet("background: transparent;")

        self._stage = QWidget()
        self._stage.setStyleSheet("background: transparent;")
        self._stage_lay = QVBoxLayout(self._stage)
        self._stage_lay.setContentsMargins(0, 0, 0, 0)
        self._stage_lay.setSpacing(0)

        self._stage_lay.addSpacing(24)

        # ── Hero Section: Emblem + Two-Tone Title + Subtitle + Divider ─────
        self._emblem = ShieldLockEmblem(self._stage)
        emblem_row = QHBoxLayout()
        emblem_row.addStretch()
        emblem_row.addWidget(self._emblem)
        emblem_row.addStretch()
        self._stage_lay.addLayout(emblem_row)
        self._stage_lay.addSpacing(4)

        self._title_lbl = QLabel(
            f"<span style='color:{LoginColors.TITLE_PERSONAL};'>Personal</span> "
            f"<span style='color:{LoginColors.TITLE_VAULT};'>Vault</span>",
            self._stage,
        )
        self._title_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._title_lbl.setFont(LoginTypography.font(Tokens.type.LOGIN_TITLE, QFont.Weight.DemiBold))
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_lbl.setMinimumHeight(40)
        self._title_lbl.setStyleSheet(
            "background: transparent; font-family: 'Inter', 'Segoe UI Variable', sans-serif; "
            f"font-size: {Tokens.type.LOGIN_TITLE}px; font-weight: 600;"
        )
        self._stage_lay.addWidget(self._title_lbl)
        self._stage_lay.addSpacing(4)

        self._subtitle_lbl = QLabel("Your private files, securely stored.", self._stage)
        self._subtitle_lbl.setFont(LoginTypography.font(Tokens.type.BODY, QFont.Weight.Normal))
        self._subtitle_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._subtitle_lbl.setMinimumHeight(18)
        self._subtitle_lbl.setStyleSheet(
            f"color: {LoginColors.SUBTITLE}; background: transparent; "
            f"font-family: 'Inter', 'Segoe UI Variable', sans-serif; font-size: {Tokens.type.BODY}px; font-weight: 400;"
        )
        self._stage_lay.addWidget(self._subtitle_lbl)
        self._stage_lay.addSpacing(10)

        self._divider = DividerWithSparkle(self._stage)
        self._divider.setFixedHeight(10)
        self._divider.setFixedWidth(240)
        div_row = QHBoxLayout()
        div_row.addStretch()
        div_row.addWidget(self._divider)
        div_row.addStretch()
        self._stage_lay.addLayout(div_row)
        self._stage_lay.addSpacing(16)

        # ── Central Card (400px clean surface on quiet #0B0D12 backdrop) ───
        self._card = LoginCard(self._stage)
        self._card.setFixedWidth(400)

        card_lay = QVBoxLayout(self._card)
        card_lay.setContentsMargins(24, 20, 24, 18)
        card_lay.setSpacing(0)

        # Master Password Label Row
        pw_hdr = QHBoxLayout()
        pw_hdr.setSpacing(8)
        pw_ico = QLabel(self._card)
        pw_ico.setFixedSize(16, 16)
        pw_ico.setPixmap(icons.get_pixmap("lock", size=15, color=LoginColors.ICE_BLUE))
        pw_ico.setStyleSheet("background: transparent;")
        pw_title = QLabel("Master Password", self._card)
        pw_title.setFont(LoginTypography.font(14, QFont.Weight.DemiBold))
        pw_title.setStyleSheet(f"color: {LoginColors.TEXT_TITLE}; background: transparent; font-size: 14px; font-weight: 600;")
        pw_hdr.addWidget(pw_ico)
        pw_hdr.addWidget(pw_title)
        pw_hdr.addStretch()
        card_lay.addLayout(pw_hdr)
        card_lay.addSpacing(4)

        # Password Input Field
        self._pw_field = PasswordInputField(self._card)
        self._pw_input = self._pw_field.line_edit
        self._pw_input.returnPressed.connect(self._attempt_login)
        self._pw_input.textChanged.connect(self._on_password_text_changed)
        card_lay.addWidget(self._pw_field)
        card_lay.addSpacing(8)

        # Key File Label Row
        kf_hdr = QHBoxLayout()
        kf_hdr.setSpacing(8)
        kf_ico = QLabel(self._card)
        kf_ico.setFixedSize(16, 16)
        kf_ico.setPixmap(icons.get_pixmap("key", size=15, color=LoginColors.ICE_BLUE))
        kf_ico.setStyleSheet("background: transparent;")
        self._kf_title_lbl = QLabel("Key File", self._card)
        self._kf_title_lbl.setFont(LoginTypography.font(14, QFont.Weight.DemiBold))
        self._kf_title_lbl.setStyleSheet(f"color: {LoginColors.TEXT_TITLE}; background: transparent; font-size: 14px; font-weight: 600;")
        kf_hdr.addWidget(kf_ico)
        kf_hdr.addWidget(self._kf_title_lbl)
        kf_hdr.addStretch()
        card_lay.addLayout(kf_hdr)
        card_lay.addSpacing(4)

        # Key File Selector Widget (aliases `_kf_widget` and `_kf_edit` for compatibility)
        self._kf_widget = KeyFileSelector(self._card)
        self._kf_edit = self._kf_widget.backing_edit
        self._kf_widget.browse_clicked.connect(self._browse_keyfile)
        card_lay.addWidget(self._kf_widget)
        card_lay.addSpacing(14)

        # Inline Error / Lockout Feedback (no intrusive modal popups)
        self._error_lbl = QLabel("", self._card)
        self._error_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error_lbl.setWordWrap(True)
        self._error_lbl.setFont(LoginTypography.font(13, QFont.Weight.Medium))
        self._error_lbl.setStyleSheet(f"color: {LoginColors.ERROR}; background: transparent;")
        self._error_lbl.setVisible(False)
        card_lay.addWidget(self._error_lbl)

        # Primary CTA: Unlock Vault
        self._unlock_btn = UnlockPrimaryButton("Unlock Vault", self._card)
        self._unlock_btn.clicked.connect(self._attempt_login)
        card_lay.addWidget(self._unlock_btn)
        card_lay.addSpacing(12)

        # Centered "Use Recovery Key" action matching reference
        rec_row = QHBoxLayout()
        rec_row.setContentsMargins(0, 0, 0, 0)
        rec_row.addStretch(1)

        self._recovery_btn = QPushButton(" Use Recovery Key", self._card)
        self._recovery_btn.setFont(LoginTypography.font(14, QFont.Weight.Medium))
        self._recovery_btn.setIcon(icons.get_icon("key", color=LoginColors.RECOVERY_BLUE, active_color=LoginColors.ICE_BLUE, size=15))
        self._recovery_btn.setIconSize(QSize(15, 15))
        self._recovery_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._recovery_btn.setToolTip("Reset your Master Password or unlock using your 256-bit recovery key")
        self._recovery_btn.setAccessibleName("Use Recovery Key")
        self._recovery_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: 2px solid transparent;
                border-radius: {Tokens.radius.CONTROL}px;
                color: {LoginColors.RECOVERY_BLUE};
                font-size: 14px;
                font-weight: 500;
                padding: 2px 8px;
                min-height: 22px;
            }}
            QPushButton:hover {{
                color: {LoginColors.ICE_BLUE};
            }}
            QPushButton:focus {{
                border: 2px solid {Tokens.colors.accent};
                color: {LoginColors.ICE_BLUE};
            }}
        """)
        self._recovery_btn.clicked.connect(self.recovery_requested.emit)
        rec_row.addWidget(self._recovery_btn)
        rec_row.addStretch(1)
        card_lay.addLayout(rec_row)

        self._switch_btn = QPushButton("Switch Vault", self._card)
        self._switch_btn.setFont(LoginTypography.font(11, QFont.Weight.Medium))
        self._switch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._switch_btn.setToolTip("Open or configure a vault at another folder location")
        self._switch_btn.setAccessibleName("Switch Vault")
        self._switch_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: 2px solid transparent;
                border-radius: 4px;
                color: transparent;
                font-size: 11px;
                padding: 0px 4px;
                min-height: 16px;
            }}
            QPushButton:hover, QPushButton:focus {{
                color: {LoginColors.LINK_HOVER};
            }}
            QPushButton:focus {{
                border: 2px solid {Tokens.colors.accent};
            }}
        """)
        self._switch_btn.clicked.connect(self.switch_vault_requested.emit)

        switch_row = QHBoxLayout()
        switch_row.setContentsMargins(0, 0, 0, 0)
        switch_row.addStretch(1)
        switch_row.addWidget(self._switch_btn)
        switch_row.addStretch(1)
        card_lay.addLayout(switch_row)

        card_center_row = QHBoxLayout()
        card_center_row.addStretch()
        card_center_row.addWidget(self._card)
        card_center_row.addStretch()
        self._stage_lay.addLayout(card_center_row)
        self._stage_lay.addSpacing(14)

        # ── Bottom Honest Security Status Badge ────────────────────────────
        self._status_badge = SecurityStatusBadge(self._stage)
        self._status_badge.setFixedHeight(24)
        self._status_badge.set_reduced_motion(reduced)
        badge_row = QHBoxLayout()
        badge_row.addStretch()
        badge_row.addWidget(self._status_badge)
        badge_row.addStretch()
        self._stage_lay.addLayout(badge_row)

        self._stage_lay.addStretch(1)

        self._scroll.setWidget(self._stage)
        root_lay.addWidget(self._scroll)

        # Explicit Tab Navigation Order (§38):
        # Password input -> Browse button -> Unlock CTA -> Recovery Key -> Switch Vault
        QWidget.setTabOrder(self._pw_input, self._kf_widget.browse_btn)
        QWidget.setTabOrder(self._kf_widget.browse_btn, self._unlock_btn)
        QWidget.setTabOrder(self._unlock_btn, self._recovery_btn)
        QWidget.setTabOrder(self._recovery_btn, self._switch_btn)

        self._update_keyfile_state()
        self._update_security_status()

    # ── Responsive Geometry & Lifecycle Cleanup ───────────────────────────────

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._backdrop.setGeometry(self.rect())

        w = self.width()
        h = self.height()

        # Adapt card width gracefully (400px target, clamped 360..400px)
        target_card_w = max(360, min(400, w - 48))
        self._card.setFixedWidth(target_card_w)
        self._divider.setFixedWidth(min(280, max(200, target_card_w - 60)))

        # Keep type scale strictly within Tokens (20px compact or 28px login title)
        if h < 660:
            title_fs = Tokens.type.PAGE_TITLE
            sub_fs = Tokens.type.LABEL
            self._stage_lay.setContentsMargins(16, 8, 16, 8)
        else:
            title_fs = Tokens.type.LOGIN_TITLE
            sub_fs = Tokens.type.BODY
            self._stage_lay.setContentsMargins(24, 16, 24, 16)

        self._title_lbl.setStyleSheet(
            "background: transparent; font-family: 'Inter', 'Segoe UI Variable', sans-serif; "
            f"font-size: {title_fs}px; font-weight: 600;"
        )
        self._title_lbl.setMinimumHeight(int(title_fs * 1.45))
        self._subtitle_lbl.setStyleSheet(
            f"color: {LoginColors.SUBTITLE}; background: transparent; "
            f"font-family: 'Inter', 'Segoe UI Variable', sans-serif; font-size: {sub_fs}px; font-weight: 400;"
        )
        self._subtitle_lbl.setMinimumHeight(int(sub_fs * 1.4))

        # Keep circular top halo aligned behind emblem
        self._sync_backdrop_anchor()

    def _sync_backdrop_anchor(self) -> None:
        if not self._emblem or not self._backdrop:
            return
        pt = self._emblem.mapTo(self, self._emblem.rect().center())
        if pt.y() > 0:
            self._backdrop.set_hero_anchor_y(pt.y())

    def showEvent(self, event) -> None:
        super().showEvent(event)
        reduced = self._is_reduced_motion()
        self._backdrop.set_reduced_motion(reduced)
        self._status_badge.set_reduced_motion(reduced)

        self._stop_pending_transition()
        self._attempt_in_progress = False
        self._card.reset_visual_state()
        self._unlock_btn.reset_state()
        self._pw_field.set_error_state(False)
        self._pw_input.setEnabled(True)
        self._pw_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._pw_field.sync_eye_icon_from_mode()
        self._pw_input.clear()
        self._clear_error()

        self._update_keyfile_state()
        self._update_security_status()
        self._check_initial_lockout()

        self._emblem.play_entrance(reduced_motion=reduced)
        self._backdrop.start_ambient()
        self._backdrop.setGeometry(self.rect())
        self._sync_backdrop_anchor()
        self._pw_input.setFocus()

    def hideEvent(self, event) -> None:
        self._stop_animations_and_timers()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:
        self.cleanup()
        super().closeEvent(event)

    def _stop_pending_transition(self) -> None:
        self._success_timer.stop()
        self._pending_success = None

    def _stop_animations_and_timers(self) -> None:
        self._stop_pending_transition()
        if self._lockout_timer is not None:
            self._lockout_timer.stop()
        self._backdrop.stop_ambient()
        self._emblem.stop_all()
        self._card.stop_all()
        self._pw_field.stop_all()
        self._unlock_btn.stop_all()
        self._status_badge.stop_all()

    def cleanup(self) -> None:
        """Stop all timers/animations and clean background resources deterministically."""
        self._stop_animations_and_timers()
        worker = self._worker
        self._worker = None
        if worker is not None:
            try:
                import shiboken6
                if shiboken6.isValid(worker) and worker.isRunning():
                    worker.wait(1500)
            except Exception:
                pass
        for th in list(self._photo_threads):
            try:
                import shiboken6
                if shiboken6.isValid(th) and th.isRunning():
                    th.wait(1000)
            except Exception:
                pass
        self._photo_threads.clear()

    # ── Keystore, Security Status & Lockout Checks ────────────────────────────

    def _update_keyfile_state(self) -> None:
        """Detect whether vault requires a keyfile cleanly without changing config semantics."""
        is_kf = False
        if self._keystore and hasattr(self._keystore, "config") and self._keystore.config.exists():
            try:
                self._keystore.config.load()
                is_kf = bool(getattr(self._keystore.config, "keyfile_enabled", False))
            except Exception:
                is_kf = False
        self._keyfile_required = is_kf
        self._kf_widget.set_required(is_kf)
        self._kf_widget.setVisible(True)

    def _update_security_status(self) -> None:
        """Reflect true vault protection readiness (never claims active on missing config)."""
        configured = False
        if self._keystore and hasattr(self._keystore, "config") and self._keystore.config.exists():
            try:
                self._keystore.config.load()
                configured = True
            except Exception:
                configured = False

        if configured and self._lockout is not None:
            self._status_badge.set_protection_active(True, "Vault protection active")
        elif configured:
            self._status_badge.set_protection_active(True, "Vault encrypted & ready")
        else:
            self._status_badge.set_protection_active(False, "Vault configuration needed")

    def _check_initial_lockout(self) -> None:
        if not self._lockout:
            return
        remaining = self._lockout.get_lockout_seconds()
        if remaining > 0:
            self._show_lockout(f"Too many failed attempts. Wait {remaining:.0f}s.")

    def _browse_keyfile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select Key File")
        if not path:
            return
        self._kf_widget.set_state("loading")
        QApplication.processEvents()
        self._kf_edit.setText(path)

    # ── Login Execution ───────────────────────────────────────────────────────

    def _attempt_login(self) -> None:
        if self._attempt_in_progress:
            return

        password = self._pw_input.text()

        # Immediate validation: empty password does NOT burn a lockout attempt
        if not password:
            self._show_error("Please enter your master password.")
            self._card.trigger_shake(reduced_motion=self._is_reduced_motion())
            self._pw_field.set_error_state(True)
            self._pw_input.setFocus()
            return

        # Check active lockout
        if self._lockout:
            try:
                self._lockout.check_lockout()
            except Exception as exc:
                self._show_lockout(str(exc))
                return

        kf_raw = self._kf_edit.text().strip()
        kf_path: Optional[str] = kf_raw or None

        if self._keyfile_required:
            if not kf_path or not os.path.isfile(kf_path):
                self._kf_widget.set_state("error")
                self._show_error("Please select a valid USB key file before unlocking.")
                self._card.trigger_shake(reduced_motion=self._is_reduced_motion())
                self._kf_edit.setFocus()
                return
            try:
                with open(kf_path, "rb") as fh:
                    fh.read(1)
            except OSError:
                self._kf_widget.set_state("error")
                self._show_error("Unable to read key file.")
                self._card.trigger_shake(reduced_motion=self._is_reduced_motion())
                return
        elif kf_path is not None:
            # Optional field populated — ensure it is readable if supplied
            if not os.path.isfile(kf_path):
                self._kf_widget.set_state("error")
                self._show_error("Selected key file could not be read.")
                return

        # Set UI busy state with immediate responsive feedback
        self._attempt_in_progress = True
        self._clear_error()
        self._pw_field.set_error_state(False)
        self._unlock_btn.set_loading(True)
        self._pw_input.setEnabled(False)

        worker = LoginWorker(
            keystore=self._keystore,
            lockout=self._lockout,
            password=password,
            keyfile_path=kf_path,
            parent=self,
        )
        self._worker = worker
        worker.success.connect(self._on_worker_success)
        worker.failure.connect(self._on_worker_failure)
        worker.error.connect(self._on_worker_error)
        worker.finished.connect(self._on_worker_finished)
        worker.start()

    def _on_worker_finished(self) -> None:
        sender = self.sender()
        if sender is self._worker:
            self._worker = None
        if sender is not None:
            sender.deleteLater()

    def _on_worker_success(self, master_key: bytearray, is_decoy: bool) -> None:
        self.last_unlock_seconds = float(getattr(self._worker, "elapsed_s", 0.0)) if self._worker else 0.0
        self._pw_input.clear()

        reduced = self._is_reduced_motion()
        self._unlock_btn.set_success()
        self._card.trigger_success(reduced_motion=reduced)
        self._emblem.flash_success(reduced_motion=reduced)

        self._stop_pending_transition()
        self._pending_success = (master_key, is_decoy)
        delay = 0 if reduced else 220
        self._success_timer.start(delay)

    def _emit_pending_success(self) -> None:
        payload = self._pending_success
        self._pending_success = None
        if payload is None:
            return
        master_key, is_decoy = payload
        self._attempt_in_progress = False
        self._unlock_btn.reset_state()
        self._pw_input.setEnabled(True)
        self.login_success.emit(master_key, is_decoy)

    def _on_worker_failure(self, msg: str, count: int, remaining: float) -> None:
        self._attempt_in_progress = False
        self._unlock_btn.reset_state()
        self._pw_input.setEnabled(True)
        self._pw_field.set_error_state(True)
        self._card.trigger_shake(reduced_motion=self._is_reduced_motion())
        self._pw_input.selectAll()
        self._pw_input.setFocus()

        if remaining > 0:
            self._show_lockout(f"Too many failed attempts. Wait {remaining:.0f}s.")
        else:
            self._show_error(msg)

        # Intruder Webcam Capture on failed attempt:
        # Always capture a photo whenever ANYONE enters a wrong password
        if self._lockout:
            QTimer.singleShot(50, self._capture_intruder_photo)

        # Optional Auto Self-Destruct after 20 failures (only if explicitly enabled)
        if (
            self._settings
            and self._settings.get("self_destruct_enabled", False)
            and count >= 20
            and self._keystore
        ):
            try:
                self._keystore.self_destruct()
                self._show_error("Vault self-destruct activated after 20 failed attempts.")
            except Exception:
                pass

    def _capture_intruder_photo(self) -> None:
        if not self._lockout:
            return
        try:
            from ..system.webcam import IntruderPhotoCaptureThread
            thread = IntruderPhotoCaptureThread(self._lockout.vault_dir, self._lockout, parent=self)
            self._photo_threads.append(thread)
            thread.finished.connect(
                lambda: self._photo_threads.remove(thread) if thread in self._photo_threads else None
            )
            thread.start()
        except Exception as exc:
            logger.warning("Failed to start background intruder capture thread: %s", exc)

    def _on_worker_error(self, err_msg: str) -> None:
        self._attempt_in_progress = False
        self._unlock_btn.reset_state()
        self._pw_input.setEnabled(True)
        self._pw_field.set_error_state(True)
        self._show_error(err_msg)

    # ── Lockout & Error Handling ──────────────────────────────────────────────

    def _clear_error(self) -> None:
        self._error_lbl.setText("")
        self._error_lbl.setVisible(False)

    def _show_error(self, msg: str) -> None:
        self._error_lbl.setStyleSheet(f"color: {LoginColors.ERROR}; background: transparent;")
        self._error_lbl.setText(msg)
        self._error_lbl.setVisible(True)

    def _show_lockout(self, msg: str) -> None:
        self._error_lbl.setStyleSheet(f"color: {LoginColors.WARNING}; background: transparent;")
        self._error_lbl.setText(f"Locked out — {msg}")
        self._error_lbl.setVisible(True)
        self._unlock_btn.setEnabled(False)
        self._pw_input.setEnabled(False)

        if self._lockout_timer:
            self._lockout_timer.stop()
        self._lockout_timer = QTimer(self)
        self._lockout_timer.timeout.connect(self._update_lockout_countdown)
        self._lockout_timer.start(1000)

    def _update_lockout_countdown(self) -> None:
        if not self._lockout:
            return
        remaining = self._lockout.get_lockout_seconds()
        if remaining <= 0:
            if self._lockout_timer:
                self._lockout_timer.stop()
            self._clear_error()
            self._pw_field.set_error_state(False)
            self._unlock_btn.setEnabled(True)
            self._pw_input.setEnabled(True)
            self._pw_input.setFocus()
        else:
            self._error_lbl.setText(f"Locked out — Too many failed attempts. Wait {remaining:.0f}s.")

    def _on_password_text_changed(self, _text: str) -> None:
        self._pw_field.set_error_state(False)
        if self._error_lbl.isVisible() and not self._error_lbl.text().startswith("Locked out"):
            self._clear_error()

    def _toggle_password_visibility(self) -> None:
        self._pw_field.toggle_visibility()

    def set_keystore(self, ks, lm, settings_store=None) -> None:
        self._keystore = ks
        self._lockout = lm
        if settings_store is not None:
            self._settings = settings_store
        reduced = self._is_reduced_motion()
        self._backdrop.set_reduced_motion(reduced)
        self._status_badge.set_reduced_motion(reduced)
        self._update_keyfile_state()
        self._update_security_status()
        self._check_initial_lockout()
