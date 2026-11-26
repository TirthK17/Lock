"""
setup_wizard.py — First-run setup wizard.

Steps (Phase 0 Audit Flow):
  1. Welcome + choose vault location
  2. Set master password (strength meter, 12-char minimum)
  3. Optional USB key-file -> "Create Vault" button
  4. Processing: Argon2id benchmark + vault creation in worker thread with progress bar
  5. Show Recovery Key: Base32 grouped key with Copy / Save to File + Acknowledgment
  6. Verification: User must re-type 3 randomly chosen groups of the recovery key -> Finish
"""

from __future__ import annotations

import logging
import os
import random
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from mylocker.app.ui import icons
from mylocker.app.ui.components import (
    Card,
    DialogShell,
    PrimaryButton,
    SecondaryButton,
    SectionHeader,
)
from mylocker.app.ui.design_system import ThemePalette, Tokens
from mylocker.app.ui.motion import crossfade_stack_page
from mylocker.app.ui.widgets import FadeLabel, PasswordStrengthBar

logger = logging.getLogger(__name__)


class SetupWorker(QObject):
    """Runs the blocking vault setup (Argon2id benchmark) in a thread."""

    finished = Signal(object, str)  # (master_key, recovery_b32)
    error = Signal(str)
    progress = Signal(str)

    def __init__(self, keystore, password: str, keyfile: Optional[str]) -> None:
        super().__init__()
        self._keystore = keystore
        self._password = password
        self._keyfile = keyfile

    def run(self) -> None:
        try:
            self.progress.emit("Calibrating Argon2id security… (~0.25s)")
            mk, rec = self._keystore.setup(self._password, self._keyfile, benchmark=True)
            self.finished.emit(mk, rec)
        except Exception as exc:
            self.error.emit(str(exc))


class SetupWizard(QWidget):
    """Multi-step setup wizard with 5-step top progress indicator."""

    setup_complete = Signal(object)          # master_key (bytearray)
    vault_location_chosen = Signal(object)   # Path
    _bl_status_ready = Signal(bool, str)

    _STEP_TITLES = ("Location", "Password", "Key File", "Encryption", "Recovery")

    def __init__(self, keystore, vault_dir_default: Path,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._keystore = keystore
        self._vault_dir = vault_dir_default
        self._keyfile: Optional[str] = None
        self._recovery_key: Optional[str] = None
        self._recovery_groups: list[str] = []
        self._verify_indices: list[int] = []
        self._master_key: Optional[bytearray] = None
        self._worker_thread: Optional[QThread] = None
        self._current_step = 0

        self._bl_status_ready.connect(self._on_bitlocker_checked)
        self._build_ui()
        Tokens.subscribe(self._on_theme)
        self.destroyed.connect(self._cleanup_sub)
        self._apply_theme()
        self._start_bitlocker_check_async(self._vault_dir)

    def _cleanup_sub(self) -> None:
        Tokens.unsubscribe(self._on_theme)

    def _on_theme(self, _pal: ThemePalette) -> None:
        self._apply_theme()

    def _apply_theme(self) -> None:
        p = Tokens.colors
        self.setStyleSheet(f"background-color: {p.bg};")
        if hasattr(self, "_rec_display"):
            self._rec_display.setStyleSheet(f"""
                background-color: {p.input_bg};
                color: {p.accent};
                border: 1px solid {p.border};
                border-radius: {Tokens.radius.CONTROL}px;
                padding: 14px;
                line-height: 1.5;
            """)
        self._update_dots(self._current_step)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        # 5-step progress indicator bar at top
        self._dots_row = QHBoxLayout()
        self._dots_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._dots_row.setSpacing(8)
        self._dots: list[QLabel] = []
        for idx, step_name in enumerate(self._STEP_TITLES):
            pill = QLabel(f"{idx + 1}. {step_name}", self)
            pill.setFont(Tokens.type.font(Tokens.type.META, Tokens.type.WEIGHT_LABEL))
            pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pill.setMinimumWidth(82)
            pill.setFixedHeight(26)
            self._dots.append(pill)
            self._dots_row.addWidget(pill)
        root.addLayout(self._dots_row)

        # Stacked pages
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_welcome())     # 0: Welcome & Location
        self._stack.addWidget(self._build_password())    # 1: Password
        self._stack.addWidget(self._build_keyfile())     # 2: Keyfile & "Create Vault"
        self._stack.addWidget(self._build_processing())  # 3: Benchmarking Worker
        self._stack.addWidget(self._build_recovery())    # 4: Display Recovery Key
        self._stack.addWidget(self._build_verify())      # 5: Verify 3 Groups & Finish

        root.addWidget(self._stack, 1)
        self._update_dots(0)

    def _card(self, width: int = 500) -> tuple[Card, QVBoxLayout]:
        card = Card(self)
        card.setObjectName("WizardCard")
        card.setMaximumWidth(width)
        card.setMinimumWidth(min(400, width))
        layout = QVBoxLayout(card)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(14)
        return card, layout

    def _page(self, widget: QWidget) -> QWidget:
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }")

        page = QWidget()
        page.setStyleSheet("background: transparent;")
        v = QVBoxLayout(page)
        v.setContentsMargins(16, 8, 16, 16)
        v.addStretch(1)
        h = QHBoxLayout()
        h.addStretch(1)
        h.addWidget(widget)
        h.addStretch(1)
        v.addLayout(h)
        v.addStretch(1)
        scroll.setWidget(page)
        return scroll

    # ── Page 0: Welcome ───────────────────────────────────────────────────────

    def _build_welcome(self) -> QWidget:
        p = Tokens.colors
        card, lay = self._card()
        lay.addWidget(self._icon("shield", 40, p.accent))
        lay.addWidget(self._heading("Create Personal Vault"))
        lay.addWidget(self._subtext(
            "Files, photos, and videos are encrypted locally with AES-256-GCM "
            "and protected by Argon2id. Only you hold the keys."
        ))

        lay.addWidget(SectionHeader("Vault Location", card))
        loc_row = QHBoxLayout()
        loc_row.setSpacing(8)
        self._loc_edit = QLineEdit(str(self._vault_dir), card)
        self._loc_edit.setReadOnly(True)
        self._loc_edit.setAccessibleName("Vault folder path")
        browse_btn = SecondaryButton("Browse…", parent=card)
        browse_btn.setFixedWidth(96)
        browse_btn.clicked.connect(self._browse_vault_dir)
        loc_row.addWidget(self._loc_edit, 1)
        loc_row.addWidget(browse_btn)
        lay.addLayout(loc_row)

        self._bl_lbl = QLabel("Checking drive encryption status…", card)
        self._bl_lbl.setWordWrap(True)
        self._bl_lbl.setFont(Tokens.type.font(Tokens.type.META, Tokens.type.WEIGHT_BODY))
        self._bl_lbl.setStyleSheet(f"color: {p.text_muted}; background: transparent;")
        lay.addWidget(self._bl_lbl)
        lay.addSpacing(8)

        self._next_btn_0 = PrimaryButton("Continue", icon_name="chevron_right", parent=card)
        self._next_btn_0.setFixedHeight(42)
        self._next_btn_0.clicked.connect(lambda: self._go(1))
        lay.addWidget(self._next_btn_0)
        return self._page(card)

    def _start_bitlocker_check_async(self, drive_path: Path) -> None:
        import threading

        def _probe() -> None:
            try:
                from ..system.bitlocker import check_bitlocker_status
                is_bl, msg = check_bitlocker_status(drive_path)
            except Exception:
                is_bl, msg = False, "For maximum offline protection, consider enabling BitLocker on this drive."
            try:
                self._bl_status_ready.emit(is_bl, msg)
            except (RuntimeError, AttributeError):
                pass

        threading.Thread(target=_probe, daemon=True, name="BitLockerCheck").start()

    def _on_bitlocker_checked(self, is_bl: bool, msg: str) -> None:
        p = Tokens.colors
        if hasattr(self, "_bl_lbl"):
            self._bl_lbl.setText(msg)
            self._bl_lbl.setStyleSheet(
                f"color: {p.success if is_bl else p.warning}; font-size: {Tokens.type.META}px; background: transparent;"
            )

    def _browse_vault_dir(self) -> None:
        import secrets

        from mylocker.app.core.keystore import Keystore
        d = QFileDialog.getExistingDirectory(self, "Choose Vault Location", str(self._vault_dir))
        if d:
            self._vault_dir = Path(d) / f"pv_{secrets.token_hex(6)}"
            self._loc_edit.setText(str(self._vault_dir))
            self._keystore = Keystore(self._vault_dir)
            self._start_bitlocker_check_async(self._vault_dir)
            self.vault_location_chosen.emit(self._vault_dir)

    def set_vault_dir(self, vault_dir: Path) -> None:
        """Synchronize the vault directory and internal Keystore instance."""
        from mylocker.app.core.keystore import Keystore
        self._vault_dir = Path(vault_dir).resolve()
        if hasattr(self, "_loc_edit"):
            self._loc_edit.setText(str(self._vault_dir))
        self._keystore = Keystore(self._vault_dir)

    # ── Page 1: Password ─────────────────────────────────────────────────────

    def _build_password(self) -> QWidget:
        card, lay = self._card()
        lay.addWidget(self._icon("lock", 36, Tokens.colors.accent))
        lay.addWidget(self._heading("Set Master Password"))
        lay.addWidget(self._subtext(
            "Use at least 12 characters combining uppercase, lowercase, digits, and symbols."
        ))

        self._pw1 = QLineEdit(card)
        self._pw1.setEchoMode(QLineEdit.EchoMode.Password)
        self._pw1.setPlaceholderText("Master password…")
        self._pw1.setAccessibleName("Master password")
        self._pw1.setFixedHeight(42)
        self._pw1.textChanged.connect(self._on_pw_changed)

        self._pw2 = QLineEdit(card)
        self._pw2.setEchoMode(QLineEdit.EchoMode.Password)
        self._pw2.setPlaceholderText("Confirm password…")
        self._pw2.setAccessibleName("Confirm master password")
        self._pw2.setFixedHeight(42)

        self._pw1_eye = self._attach_eye_toggle(self._pw1)
        self._pw2_eye = self._attach_eye_toggle(self._pw2)
        self._pw1.returnPressed.connect(self._pw2.setFocus)
        self._pw2.returnPressed.connect(self._validate_password)

        self._strength_bar = PasswordStrengthBar()
        self._pw_error = FadeLabel("")

        lay.addWidget(self._pw1)
        lay.addWidget(self._strength_bar)
        lay.addWidget(self._pw2)
        lay.addWidget(self._pw_error)

        btn_row = QHBoxLayout()
        back = SecondaryButton("Back", icon_name="chevron_left", parent=card)
        back.clicked.connect(lambda: self._go(0))
        self._next_btn_1 = PrimaryButton("Continue", icon_name="chevron_right", parent=card)
        self._next_btn_1.clicked.connect(self._validate_password)
        btn_row.addWidget(back)
        btn_row.addStretch()
        btn_row.addWidget(self._next_btn_1)
        lay.addLayout(btn_row)
        return self._page(card)

    def _on_pw_changed(self, text: str) -> None:
        self._strength_bar.update_password(text)

    def _validate_password(self) -> None:
        pw1 = self._pw1.text()
        pw2 = self._pw2.text()
        from mylocker.app.core.password_policy import validate_password
        ok, msg, _ = validate_password(pw1)
        if not ok:
            self._pw_error.show_text(msg, Tokens.colors.danger)
            return
        if pw1 != pw2:
            self._pw_error.show_text("Passwords do not match.", Tokens.colors.danger)
            return
        self._go(2)

    # ── Page 2: Key file ──────────────────────────────────────────────────────

    def _build_keyfile(self) -> QWidget:
        card, lay = self._card()
        lay.addWidget(self._icon("key", 36, Tokens.colors.accent))
        lay.addWidget(self._heading("Optional USB Key File"))
        lay.addWidget(self._subtext(
            "A physical key file acts as a hardware second factor. "
            "Without both your password and the file, the vault cannot unlock."
        ))

        self._kf_check = QCheckBox("Require a key file as second factor", card)
        self._kf_check.toggled.connect(self._on_kf_toggled)
        lay.addWidget(self._kf_check)

        self._kf_row = QHBoxLayout()
        self._kf_row.setSpacing(8)
        self._kf_edit = QLineEdit(card)
        self._kf_edit.setPlaceholderText("Path to key file…")
        self._kf_edit.setAccessibleName("Key file path")
        self._kf_edit.setEnabled(False)
        self._kf_browse = SecondaryButton("Browse…", parent=card)
        self._kf_browse.setFixedWidth(96)
        self._kf_browse.setEnabled(False)
        self._kf_browse.clicked.connect(self._browse_keyfile)
        self._kf_row.addWidget(self._kf_edit, 1)
        self._kf_row.addWidget(self._kf_browse)
        lay.addLayout(self._kf_row)
        lay.addSpacing(8)

        btn_row = QHBoxLayout()
        back = SecondaryButton("Back", icon_name="chevron_left", parent=card)
        back.clicked.connect(lambda: self._go(1))
        create_btn = PrimaryButton("Create Vault", icon_name="shield_check", parent=card)
        create_btn.clicked.connect(self._on_create_vault_clicked)
        btn_row.addWidget(back)
        btn_row.addStretch()
        btn_row.addWidget(create_btn)
        lay.addLayout(btn_row)
        return self._page(card)

    def _on_kf_toggled(self, checked: bool) -> None:
        self._kf_edit.setEnabled(checked)
        self._kf_browse.setEnabled(checked)

    def _browse_keyfile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select Key File")
        if path:
            self._kf_edit.setText(path)

    def _on_create_vault_clicked(self) -> None:
        if self._kf_check.isChecked():
            path = self._kf_edit.text().strip()
            if not path or not os.path.exists(path):
                DialogShell.warn(self, "Key File Required", "Please select a valid key file.")
                return
            self._keyfile = path
        else:
            self._keyfile = None
        self._start_setup()

    # ── Page 3: Processing (Argon2id benchmark in worker) ─────────────────────

    def _build_processing(self) -> QWidget:
        card, lay = self._card()
        lay.addWidget(self._icon("settings", 36, Tokens.colors.accent))
        lay.addWidget(self._heading("Benchmarking & Creating Vault"))
        self._proc_status = QLabel("Calibrating Argon2id key derivation (~0.25s)…", card)
        self._proc_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._proc_status.setFont(Tokens.type.font(Tokens.type.BODY, Tokens.type.WEIGHT_BODY))
        self._proc_status.setProperty("role", "secondary")
        self._proc_bar = QProgressBar(card)
        self._proc_bar.setRange(0, 0)
        self._proc_bar.setTextVisible(False)
        self._proc_bar.setFixedHeight(8)

        lay.addWidget(self._proc_status)
        lay.addWidget(self._proc_bar)
        return self._page(card)

    def _start_setup(self) -> None:
        self._go(3)
        password = self._pw1.text()
        self._worker_thread = QThread()
        self._worker = SetupWorker(self._keystore, password, self._keyfile)
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_setup_done)
        self._worker.error.connect(self._on_setup_error)
        self._worker.progress.connect(lambda msg: self._proc_status.setText(msg))
        self._worker_thread.start()

    def _on_setup_done(self, mk: bytearray, recovery_b32: str) -> None:
        if self._worker_thread:
            self._worker_thread.quit()
            self._worker_thread.wait(3000)
        self._pw1.clear()
        self._pw2.clear()
        self._master_key = mk
        self._recovery_key = recovery_b32
        self._recovery_groups = recovery_b32.split("-")
        self._rec_display.setText(recovery_b32)
        self._copy_btn.setEnabled(True)
        self._save_btn.setEnabled(True)
        self._ack_check.setChecked(False)
        self._rec_continue_btn.setEnabled(False)
        self._go(4)

    def _on_setup_error(self, msg: str) -> None:
        if self._worker_thread:
            self._worker_thread.quit()
            self._worker_thread.wait(3000)
        logger.error("Setup worker failed: %s", msg)
        DialogShell.error(
            self,
            "Setup Failed",
            "Could not create vault. Please check the selected directory and try again. [ERR-SETUP-02]",
        )
        self._go(1)

    # ── Page 4: Recovery key display ──────────────────────────────────────────

    def _build_recovery(self) -> QWidget:
        card, lay = self._card(520)
        lay.addWidget(self._icon("key", 36, Tokens.colors.accent))
        lay.addWidget(self._heading("Save Recovery Key"))
        lay.addWidget(self._subtext(
            "Store this 256-bit recovery key safely outside your vault. "
            "It is the only way to recover access if you forget your password."
        ))

        self._rec_display = QLabel("", card)
        self._rec_display.setFont(QFont("Cascadia Code, Consolas, Courier New", 12, QFont.Weight.Bold))
        self._rec_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._rec_display.setWordWrap(True)
        self._rec_display.setMinimumHeight(76)
        lay.addWidget(self._rec_display)

        copy_row = QHBoxLayout()
        copy_row.setSpacing(8)
        self._copy_btn = SecondaryButton("Copy Key", icon_name="copy", parent=card)
        self._copy_btn.clicked.connect(self._copy_recovery)

        self._save_btn = SecondaryButton("Save to File", icon_name="download", parent=card)
        self._save_btn.clicked.connect(self._save_recovery)

        copy_row.addWidget(self._copy_btn)
        copy_row.addWidget(self._save_btn)
        lay.addLayout(copy_row)

        ack_row = QHBoxLayout()
        self._ack_check = QCheckBox(card)
        self._ack_check.toggled.connect(self._on_ack_toggled)
        ack_label = QLabel(
            "I have saved my recovery key in a safe location.", card
        )
        ack_label.setWordWrap(True)
        ack_label.setFont(Tokens.type.font(Tokens.type.LABEL, Tokens.type.WEIGHT_LABEL))
        ack_row.addWidget(self._ack_check, alignment=Qt.AlignmentFlag.AlignVCenter)
        ack_row.addWidget(ack_label, stretch=1)
        lay.addLayout(ack_row)

        btn_row = QHBoxLayout()
        self._rec_continue_btn = PrimaryButton("Verify Key", icon_name="chevron_right", parent=card)
        self._rec_continue_btn.setEnabled(False)
        self._rec_continue_btn.clicked.connect(self._prepare_verify_page)
        btn_row.addStretch()
        btn_row.addWidget(self._rec_continue_btn)
        lay.addLayout(btn_row)
        return self._page(card)

    def _on_ack_toggled(self, checked: bool) -> None:
        self._rec_continue_btn.setEnabled(checked)

    # ── Page 5: Verification (re-type 3 randomly chosen groups) ───────────────

    def _build_verify(self) -> QWidget:
        card, lay = self._card(500)
        lay.addWidget(self._icon("shield_check", 36, Tokens.colors.success))
        lay.addWidget(self._heading("Confirm Recovery Key"))
        self._verify_subtext = QLabel(
            "Enter the 3 requested groups below to confirm your recovery key backup:", card
        )
        self._verify_subtext.setFont(Tokens.type.font(Tokens.type.BODY, Tokens.type.WEIGHT_BODY))
        self._verify_subtext.setProperty("role", "secondary")
        self._verify_subtext.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._verify_subtext.setWordWrap(True)
        lay.addWidget(self._verify_subtext)

        self._verify_labels: list[QLabel] = []
        self._verify_edits: list[QLineEdit] = []
        for i in range(3):
            lbl = QLabel(f"Group #{i+1}:", card)
            lbl.setFont(Tokens.type.font(Tokens.type.LABEL, Tokens.type.WEIGHT_LABEL))
            edit = QLineEdit(card)
            edit.setFont(QFont("Cascadia Code, Consolas", 12))
            edit.setFixedHeight(38)
            edit.setPlaceholderText("Enter 8-character group…")
            edit.textChanged.connect(self._check_verification)
            edit.returnPressed.connect(self._on_verify_enter)
            self._verify_labels.append(lbl)
            self._verify_edits.append(edit)
            lay.addWidget(lbl)
            lay.addWidget(edit)

        self._verify_error = FadeLabel("")
        lay.addWidget(self._verify_error)

        btn_row = QHBoxLayout()
        back = SecondaryButton("Back", icon_name="chevron_left", parent=card)
        back.clicked.connect(lambda: self._go(4))
        self._finish_btn = PrimaryButton("Finish & Open Vault", icon_name="check", parent=card)
        self._finish_btn.setEnabled(False)
        self._finish_btn.clicked.connect(self._finish_setup)
        btn_row.addWidget(back)
        btn_row.addStretch()
        btn_row.addWidget(self._finish_btn)
        lay.addLayout(btn_row)
        return self._page(card)

    def _prepare_verify_page(self) -> None:
        """Choose 3 random groups of the recovery key for the user to confirm."""
        num_groups = len(self._recovery_groups)
        if num_groups < 3:
            self._verify_indices = list(range(num_groups))
        else:
            self._verify_indices = sorted(random.sample(range(num_groups), 3))

        for i, idx in enumerate(self._verify_indices):
            self._verify_labels[i].setText(f"Group #{idx + 1} (characters {(idx * 8) + 1}–{(idx + 1) * 8}):")
            self._verify_edits[i].clear()

        self._finish_btn.setEnabled(False)
        self._verify_error.show_text("", Tokens.colors.text_secondary)
        self._go(5)

    def _check_verification(self) -> None:
        all_correct = True
        for i, idx in enumerate(self._verify_indices):
            entered = self._verify_edits[i].text().strip().upper()
            expected = self._recovery_groups[idx].upper()
            if entered != expected:
                all_correct = False
                break
        self._finish_btn.setEnabled(all_correct)

    def _finish_setup(self) -> None:
        logger.info("Recovery key confirmed successfully. Finishing wizard.")
        self.setup_complete.emit(self._master_key)

    def _on_verify_enter(self) -> None:
        if self._finish_btn.isEnabled():
            self._finish_setup()

    def _attach_eye_toggle(self, field: QLineEdit) -> QAction:
        action = QAction(self)
        action.setIcon(icons.get_icon("eye", color=Tokens.colors.text_muted))
        action.setToolTip("Show / Hide password")

        def _toggle() -> None:
            p = Tokens.colors
            if field.echoMode() == QLineEdit.EchoMode.Password:
                field.setEchoMode(QLineEdit.EchoMode.Normal)
                action.setIcon(icons.get_icon("eye_off", color=p.accent))
            else:
                field.setEchoMode(QLineEdit.EchoMode.Password)
                action.setIcon(icons.get_icon("eye", color=p.text_muted))

        action.triggered.connect(_toggle)
        field.addAction(action, QLineEdit.ActionPosition.TrailingPosition)
        return action

    # ── Helpers & Navigation ──────────────────────────────────────────────────

    def _go(self, page: int) -> None:
        crossfade_stack_page(self._stack, page)
        self._current_step = min(page, 4)
        self._update_dots(self._current_step)

    def _update_dots(self, current: int) -> None:
        p = Tokens.colors
        for i, pill in enumerate(self._dots):
            if i == current:
                pill.setStyleSheet(
                    f"background-color: {p.accent_tint}; color: {p.text}; "
                    f"border: 1px solid {p.accent_border}; border-radius: 6px; padding: 0 8px;"
                )
            elif i < current:
                pill.setStyleSheet(
                    f"background-color: {p.success_tint}; color: {p.success}; "
                    f"border: 1px solid {p.success_border}; border-radius: 6px; padding: 0 8px;"
                )
            else:
                pill.setStyleSheet(
                    f"background-color: {p.card}; color: {p.text_muted}; "
                    f"border: 1px solid {p.border}; border-radius: 6px; padding: 0 8px;"
                )

    def _copy_recovery(self) -> None:
        QApplication.clipboard().setText(self._recovery_key or "")
        self._copy_btn.setText("Copied!")
        QTimer.singleShot(2000, lambda: self._copy_btn.setText("Copy Key"))

    def _save_recovery(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Recovery Key", "vault_recovery_key.txt", "Text (*.txt)"
        )
        if path and self._recovery_key:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(f"Personal Vault Recovery Key\n{'=' * 40}\n")
                fh.write(self._recovery_key + "\n")
                fh.write("\nStore this file securely. It can unlock your vault if you lose your password.\n")
            DialogShell.inform(self, "Saved", "Recovery key saved successfully.")

    def _icon(self, name: str, size: int, color: Optional[str] = None) -> QLabel:
        lbl = QLabel()
        lbl.setPixmap(icons.get_pixmap(name, size=size, color=color or Tokens.colors.accent))
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("background: transparent;")
        return lbl

    def _heading(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFont(Tokens.type.font(Tokens.type.PAGE_TITLE, Tokens.type.WEIGHT_TITLE))
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setWordWrap(True)
        return lbl

    def _subtext(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFont(Tokens.type.font(Tokens.type.BODY, Tokens.type.WEIGHT_BODY))
        lbl.setProperty("role", "secondary")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setWordWrap(True)
        return lbl


__all__ = ["SetupWizard"]
