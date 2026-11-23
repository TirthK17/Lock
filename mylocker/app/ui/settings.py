"""
settings.py — Comprehensive settings page for Personal Vault.

Sections:
  1. Security & Auto-Lock (idle timeout, lock on minimize, panic hotkey, screenshot protection)
  2. Decoy / Duress Vault setup
  3. Stealth & Disguise (Calculator camouflage, window renaming, tray-only)
  4. Password & Key Management (Change password, recovery key info, key rotation)
  5. Backup & Integrity (Create backup, restore backup, deep integrity check)
  6. Self-Destruct Options (off by default, dangerous)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core.backup import create_vault_backup, restore_vault_backup, rotate_vault_keys
from ..core.keystore import Keystore
from ..core.lockout import LockoutManager, make_lockout_signing_key
from ..core.password_policy import validate_password
from ..core.session import get_session
from . import icons
from .theme import Colors
from .widgets import GlowButton, ProgressOverlay, SmoothScrollArea

logger = logging.getLogger(__name__)


class _SettingsWorker(QThread):
    """Runs long-running settings operations (backup, restore, verify, rotate) off the UI thread."""

    progress = Signal(int, int, str)
    finished_result = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            res = self._fn(lambda d, t, msg="": self.progress.emit(d, t, msg))
            self.finished_result.emit(res)
        except Exception as exc:
            logger.error("Settings background task error: %s", type(exc).__name__)
            self.failed.emit("Operation failed. Please try again.")


class SettingsUI(QWidget):
    """Settings view embedded in the main dashboard."""

    settings_updated = Signal()
    lock_requested = Signal()
    vault_reloaded = Signal()
    credentials_changed = Signal()

    def __init__(
        self,
        vault,
        keystore: Keystore,
        settings_store=None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.vault = vault
        self.keystore = keystore
        self._worker: Optional[_SettingsWorker] = None

        if settings_store is not None:
            self.settings = settings_store
        else:
            from ..core.settings_store import SettingsStore
            signing_key = b"PersonalVault-DefaultSaltKey-v1"
            try:
                if self.keystore.config.exists():
                    self.keystore.config.load()
                    signing_key = make_lockout_signing_key(self.keystore.config.kdf_params.salt)
            except Exception:
                pass
            self.settings = SettingsStore(self.vault.vault_dir, signing_key)

        self._build_ui()
        self._load_stored_settings()

    def _on_setting_changed(self, key: str, val) -> None:
        self.settings.set(key, val)
        self.settings_updated.emit()

    def _load_stored_settings(self) -> None:
        self.idle_spin.setValue(int(self.settings.get("idle_timeout_minutes", 5)))
        self.lock_minimize_check.setChecked(bool(self.settings.get("lock_on_minimize", True)))
        self.screen_protect_check.setChecked(bool(self.settings.get("screenshot_protection", True)))
        self.clipboard_wipe_check.setChecked(int(self.settings.get("clipboard_wipe_seconds", 30)) > 0)
        self.webcam_check.setChecked(bool(self.settings.get("intruder_webcam_enabled", False)))
        self.calc_check.setChecked(bool(self.settings.get("calculator_disguise_enabled", False)))
        self.pin_input.setText(str(self.settings.get("calculator_disguise_pin", "1337")))
        self.hide_taskbar_check.setChecked(bool(self.settings.get("tray_only_mode", False)))
        self.destruct_check.blockSignals(True)
        self.destruct_check.setChecked(bool(self.settings.get("self_destruct_enabled", False)))
        self.destruct_check.blockSignals(False)

        # Wire update events
        self.idle_spin.valueChanged.connect(lambda v: self._on_setting_changed("idle_timeout_minutes", v))
        self.lock_minimize_check.toggled.connect(lambda c: self._on_setting_changed("lock_on_minimize", c))
        self.screen_protect_check.toggled.connect(lambda c: self._on_setting_changed("screenshot_protection", c))
        self.clipboard_wipe_check.toggled.connect(lambda c: self._on_setting_changed("clipboard_wipe_seconds", 30 if c else 0))
        self.webcam_check.toggled.connect(lambda c: self._on_setting_changed("intruder_webcam_enabled", c))
        self.calc_check.toggled.connect(lambda c: self._on_setting_changed("calculator_disguise_enabled", c))
        self.pin_input.textChanged.connect(lambda t: self._on_setting_changed("calculator_disguise_pin", t.strip()))
        self.hide_taskbar_check.toggled.connect(lambda c: self._on_setting_changed("tray_only_mode", c))
        self.destruct_check.toggled.connect(self._on_destruct_toggled)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(16)

        # Title
        header = QLabel("Vault Settings")
        header.setFont(QFont("Segoe UI Variable", 20, QFont.Weight.Bold))
        header.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; background: transparent;")
        outer.addWidget(header)

        # Scrollable settings area
        scroll = SmoothScrollArea()
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 16, 0)
        layout.setSpacing(20)

        # ── 1. Security & Auto-Lock ──
        sec_card, sec_layout = self._create_card("Security & Auto-Lock", "lock")

        # Idle timeout row
        idle_row = QHBoxLayout()
        idle_lbl = QLabel("Auto-lock after user inactivity (minutes):")
        idle_lbl.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; background: transparent;")
        self.idle_spin = QSpinBox()
        self.idle_spin.setRange(1, 120)
        self.idle_spin.setValue(5)
        self.idle_spin.setFixedWidth(70)
        idle_row.addWidget(idle_lbl)
        idle_row.addStretch(1)
        idle_row.addWidget(self.idle_spin)
        sec_layout.addLayout(idle_row)

        self.lock_minimize_check = QCheckBox("Lock vault immediately when window is minimized")
        self.lock_minimize_check.setChecked(True)
        sec_layout.addWidget(self.lock_minimize_check)

        self.screen_protect_check = QCheckBox("Block screenshots and screen recorders (WDA_EXCLUDEFROMCAPTURE)")
        self.screen_protect_check.setChecked(True)
        sec_layout.addWidget(self.screen_protect_check)

        self.clipboard_wipe_check = QCheckBox("Automatically clear clipboard 30 seconds after copying")
        self.clipboard_wipe_check.setChecked(True)
        sec_layout.addWidget(self.clipboard_wipe_check)

        self.webcam_check = QCheckBox("Capture webcam photo on failed login attempts (DPAPI encrypted)")
        self.webcam_check.setChecked(False)
        sec_layout.addWidget(self.webcam_check)

        layout.addWidget(sec_card)

        # ── 2. Stealth & Disguise ──
        stealth_card, stealth_layout = self._create_card("Stealth & Camouflage", "calculator")

        self.calc_check = QCheckBox("Enable Calculator Disguise (secret PIN opens real vault)")
        self.calc_check.setChecked(False)
        stealth_layout.addWidget(self.calc_check)

        pin_row = QHBoxLayout()
        pin_lbl = QLabel("Disguise Secret PIN:")
        pin_lbl.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; background: transparent;")
        self.pin_input = QLineEdit("1337")
        self.pin_input.setFixedWidth(100)
        pin_row.addWidget(pin_lbl)
        pin_row.addWidget(self.pin_input)
        pin_row.addStretch(1)
        stealth_layout.addLayout(pin_row)

        self.hide_taskbar_check = QCheckBox("Run silently in system tray only (hide from taskbar)")
        self.hide_taskbar_check.setChecked(False)
        stealth_layout.addWidget(self.hide_taskbar_check)

        layout.addWidget(stealth_card)

        # ── 3. Decoy / Duress Vault ──
        decoy_card, decoy_layout = self._create_card("Decoy / Duress Vault", "shield")

        decoy_desc = QLabel(
            "Configure a separate password that opens an empty decoy vault.\n"
            "If forced to unlock your vault under duress, enter this password instead."
        )
        decoy_desc.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 12px; background: transparent;")
        decoy_desc.setWordWrap(True)
        decoy_layout.addWidget(decoy_desc)

        setup_decoy_btn = QPushButton(" Configure Decoy Password")
        setup_decoy_btn.setIcon(icons.get_icon("shield", color=Colors.TEXT_PRIMARY))
        setup_decoy_btn.setFixedWidth(220)
        setup_decoy_btn.clicked.connect(self._setup_decoy_dialog)
        decoy_layout.addWidget(setup_decoy_btn)

        layout.addWidget(decoy_card)

        # ── 4. Key Management ──
        key_card, key_layout = self._create_card("Password & Key Management", "key")

        change_pw_btn = QPushButton(" Change Master Password")
        change_pw_btn.setIcon(icons.get_icon("key", color=Colors.TEXT_PRIMARY))
        change_pw_btn.setFixedWidth(220)
        change_pw_btn.clicked.connect(self._change_password_dialog)
        key_layout.addWidget(change_pw_btn)

        rotate_key_btn = QPushButton(" Rotate Master Key (Re-encrypt All)")
        rotate_key_btn.setIcon(icons.get_icon("refresh", color=Colors.TEXT_PRIMARY))
        rotate_key_btn.setFixedWidth(260)
        rotate_key_btn.setProperty("ghost", True)
        rotate_key_btn.clicked.connect(self._rotate_keys_dialog)
        key_layout.addWidget(rotate_key_btn)

        optimize_speed_btn = QPushButton(" Optimize Unlock Speed (~0.25s)")
        optimize_speed_btn.setIcon(icons.get_icon("shield_check", color=Colors.SUCCESS))
        optimize_speed_btn.setFixedWidth(260)
        optimize_speed_btn.setToolTip(
            "Re-tunes Argon2id key derivation on this computer to unlock in ~0.25s "
            "while keeping OWASP-safe cryptographic strength (>=64 MiB)."
        )
        optimize_speed_btn.clicked.connect(self._optimize_unlock_speed_dialog)
        key_layout.addWidget(optimize_speed_btn)

        layout.addWidget(key_card)

        # ── 5. Backup & Integrity ──
        backup_card, backup_layout = self._create_card("Backup & Verification", "archive")

        backup_btns = QHBoxLayout()
        export_btn = GlowButton(" Export Encrypted Backup")
        export_btn.setIcon(icons.get_icon("download", color=Colors.TEXT_PRIMARY, active_color=Colors.WHITE))
        export_btn.clicked.connect(self._export_backup)

        restore_btn = QPushButton(" Restore from Backup")
        restore_btn.setIcon(icons.get_icon("upload", color=Colors.TEXT_PRIMARY))
        restore_btn.clicked.connect(self._restore_backup)

        verify_btn = QPushButton(" Verify Vault Integrity")
        verify_btn.setIcon(icons.get_icon("shield_check", color=Colors.TEXT_PRIMARY))
        verify_btn.clicked.connect(self._verify_vault)

        backup_btns.addWidget(export_btn)
        backup_btns.addWidget(restore_btn)
        backup_btns.addWidget(verify_btn)
        backup_btns.addStretch(1)
        backup_layout.addLayout(backup_btns)

        layout.addWidget(backup_card)

        # ── 6. Self-Destruct ──
        destruct_card, destruct_layout = self._create_card("Emergency Self-Destruct", "alert_triangle")

        destruct_desc = QLabel(
            "Permanently wipe Master Key wrapping after N failed unlock attempts or manually.\n"
            "WARNING: Once wiped, all encrypted data is unreadable forever!"
        )
        destruct_desc.setStyleSheet(f"color: {Colors.DANGER}; font-size: 12px; background: transparent;")
        destruct_desc.setWordWrap(True)
        destruct_layout.addWidget(destruct_desc)

        self.destruct_check = QCheckBox("Enable permanent self-destruct after failed attempts")
        self.destruct_check.setChecked(False)
        destruct_layout.addWidget(self.destruct_check)

        destruct_btn = QPushButton(" Destroy Vault Now (Emergency Wipe)")
        destruct_btn.setIcon(icons.get_icon("alert_triangle", color=Colors.DANGER, active_color=Colors.WHITE))
        destruct_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(255, 113, 131, 0.15);
                color: {Colors.DANGER};
                border: 1px solid {Colors.DANGER};
                border-radius: 8px;
                padding: 8px 16px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {Colors.DANGER};
                color: {Colors.WHITE};
            }}
        """)
        destruct_btn.setFixedWidth(280)
        destruct_btn.clicked.connect(self._confirm_self_destruct_dialog)
        destruct_layout.addWidget(destruct_btn)

        layout.addWidget(destruct_card)

        scroll.setWidget(container)
        outer.addWidget(scroll, 1)

    def _create_card(self, title: str, icon_key: Optional[str] = None) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("SettingsCard")
        card.setStyleSheet(f"""
            #SettingsCard {{
                background-color: {Colors.BG_CARD};
                border: 1px solid {Colors.BORDER};
                border-radius: 12px;
                padding: 16px;
            }}
        """)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)

        header_row = QHBoxLayout()
        header_row.setSpacing(8)
        if icon_key:
            icon_lbl = QLabel()
            icon_lbl.setPixmap(icons.get_pixmap(icon_key, size=20, color=Colors.PRIMARY))
            icon_lbl.setStyleSheet("background: transparent;")
            header_row.addWidget(icon_lbl)

        lbl = QLabel(title)
        lbl.setFont(QFont("Segoe UI Variable", 14, QFont.Weight.Bold))
        lbl.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; background: transparent;")
        header_row.addWidget(lbl)
        header_row.addStretch()

        lay.addLayout(header_row)
        return card, lay

    # ── Actions ───────────────────────────────────────────────────────────────

    def _run_with_overlay(self, title: str, work_fn: Callable, on_done: Callable) -> None:
        overlay = ProgressOverlay(title, self)
        overlay._cancel_btn.hide()  # backup/verify/rotate completes cleanly atomically
        overlay.show()

        worker = _SettingsWorker(work_fn, self)
        self._worker = worker
        worker.progress.connect(overlay.set_progress)

        def _finish(res: object) -> None:
            overlay.accept()
            self._worker = None
            on_done(res)

        def _err(msg: str) -> None:
            overlay.accept()
            self._worker = None
            QMessageBox.critical(self, "Operation Failed", f"{msg} [ERR-SET-01]")

        worker.finished_result.connect(_finish)
        worker.failed.connect(_err)
        worker.start()

    def _on_destruct_toggled(self, checked: bool) -> None:
        if checked:
            reply = QMessageBox.warning(
                self,
                "Enable Auto Self-Destruct?",
                "If enabled, after 20 consecutive failed unlock attempts all master key slots "
                "will be permanently wiped and vault data will be UNRECOVERABLE.\n\n"
                "Enable this setting?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                self.destruct_check.blockSignals(True)
                self.destruct_check.setChecked(False)
                self.destruct_check.blockSignals(False)
                return
        self._on_setting_changed("self_destruct_enabled", checked)

    def _export_backup(self) -> None:
        save_path, _ = QFileDialog.getSaveFileName(
            self, "Save Vault Backup", "PersonalVault_Backup.tar.gz", "Tar Gzip Archive (*.tar.gz)"
        )
        if not save_path:
            return

        vault_dir = self.vault.vault_dir
        dest = Path(save_path)

        def _work(cb):
            return create_vault_backup(vault_dir, dest, progress=cb)

        def _done(res):
            ok, _msg = res
            if ok:
                QMessageBox.information(self, "Backup Complete", "Encrypted backup created successfully.")
            else:
                QMessageBox.critical(self, "Backup Error", "Failed to create encrypted backup. Please check destination permissions and try again. [ERR-BKP-01]")

        self._run_with_overlay("Creating Encrypted Backup", _work, _done)

    def _restore_backup(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Backup Archive", "", "Tar Gzip Archive (*.tar.gz)"
        )
        if not file_path:
            return

        reply = QMessageBox.warning(
            self,
            "Confirm Restore",
            "Restoring a backup will replace your current vault data (a .old snapshot will be kept).\nAre you sure you want to proceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        pw, pw_ok = QInputDialog.getText(
            self,
            "Verify Backup Password",
            "Enter the backup archive's master password to verify integrity before restoring:",
            QLineEdit.EchoMode.Password,
        )
        if not pw_ok:
            return

        kf_path: Optional[str] = None
        if self.keystore.config.exists():
            try:
                self.keystore.config.load()
                if self.keystore.config.keyfile_enabled:
                    chosen_kf, _ = QFileDialog.getOpenFileName(self, "Select Key File (Optional)")
                    kf_path = chosen_kf or None
            except Exception:
                pass

        src = Path(file_path)
        vault_dir = self.vault.vault_dir

        def _work(cb):
            return restore_vault_backup(
                src,
                vault_dir,
                password=pw or None,
                keyfile_path=kf_path,
                progress=cb,
            )

        def _done(res):
            ok, _msg = res
            if ok:
                QMessageBox.information(self, "Restore Succeeded", "Vault restored. Please lock and re-login.")
                self.lock_requested.emit()
            else:
                QMessageBox.critical(self, "Restore Error", "Failed to restore backup. Check your password and archive integrity, then try again. [ERR-RST-01]")

        self._run_with_overlay("Restoring Backup", _work, _done)

    def _verify_vault(self) -> None:
        def _work(cb):
            return self.vault.verify_all(progress=cb)

        def _done(results):
            total = len(results)
            failures = [fid for fid, (ok, _) in results.items() if not ok]
            if not failures:
                QMessageBox.information(self, "Integrity Check", f"All {total} files verified successfully! No corruption found.")
            else:
                QMessageBox.warning(self, "Corruption Detected", f"{len(failures)} out of {total} files failed integrity verification!")

        self._run_with_overlay("Verifying Vault Integrity", _work, _done)

    def _change_password_dialog(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Change Master Password")
        dlg.setFixedSize(420, 270)
        dlg.setStyleSheet(f"background: {Colors.BG_CARD}; color: {Colors.TEXT_PRIMARY};")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(10)

        old_pw = QLineEdit()
        old_pw.setEchoMode(QLineEdit.EchoMode.Password)
        old_pw.setPlaceholderText("Current Password")

        new_pw = QLineEdit()
        new_pw.setEchoMode(QLineEdit.EchoMode.Password)
        new_pw.setPlaceholderText("New Password (min 12 chars, mixed case + digit + symbol)")

        confirm_pw = QLineEdit()
        confirm_pw.setEchoMode(QLineEdit.EchoMode.Password)
        confirm_pw.setPlaceholderText("Confirm New Password")

        lay.addWidget(old_pw)
        lay.addWidget(new_pw)
        lay.addWidget(confirm_pw)

        btn = GlowButton("Update Password")
        active_worker: list[Optional[_SettingsWorker]] = [None]

        def _set_busy(busy: bool) -> None:
            for w in (old_pw, new_pw, confirm_pw, btn):
                w.setEnabled(not busy)
            btn.setText("Updating Password…" if busy else "Update Password")

        def _do_change():
            if active_worker[0] is not None:
                return
            ok_pw, pw_msg, _ = validate_password(new_pw.text())
            if not ok_pw:
                QMessageBox.warning(dlg, "Weak Password", pw_msg)
                return
            if new_pw.text() != confirm_pw.text():
                QMessageBox.warning(dlg, "Error", "New passwords do not match.")
                return
            kf_path: Optional[str] = None
            try:
                if self.keystore.config.exists():
                    self.keystore.config.load()
                    if self.keystore.config.keyfile_enabled:
                        kf_path, _ = QFileDialog.getOpenFileName(dlg, "Select Current USB Key File")
                        if not kf_path:
                            return
            except Exception:
                QMessageBox.critical(dlg, "Error", "Could not read keystore configuration. [ERR-PW-01]")
                return

            cur_pw_val = old_pw.text()
            new_pw_val = new_pw.text()
            _set_busy(True)

            def _work(_cb):
                self.keystore.change_password(
                    cur_pw_val,
                    new_pw_val,
                    keyfile_path=kf_path,
                    old_keyfile_path=kf_path,
                )
                self.keystore.config.load()
                return make_lockout_signing_key(self.keystore.config.kdf_params.salt)

            worker = _SettingsWorker(_work, dlg)
            active_worker[0] = worker

            def _on_ok(sk: object) -> None:
                active_worker[0] = None
                worker.deleteLater()
                _set_busy(False)
                self.settings.rekey(sk)
                LockoutManager(self.vault.vault_dir, sk).rekey(sk, reset_failures=True)
                self.credentials_changed.emit()
                QMessageBox.information(dlg, "Success", "Master password updated successfully.")
                dlg.accept()

            def _on_err(_msg: str) -> None:
                active_worker[0] = None
                worker.deleteLater()
                _set_busy(False)
                QMessageBox.critical(dlg, "Error", "Failed to change password. Check current credentials and try again. [ERR-PW-01]")

            worker.finished_result.connect(_on_ok, Qt.ConnectionType.QueuedConnection)
            worker.failed.connect(_on_err, Qt.ConnectionType.QueuedConnection)
            worker.start()

        dlg.rejected.connect(lambda: active_worker[0].wait(3000) if active_worker[0] else None)
        btn.clicked.connect(_do_change)
        lay.addWidget(btn)
        dlg.exec()

    def _setup_decoy_dialog(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Setup Decoy Password")
        dlg.setFixedSize(380, 220)
        dlg.setStyleSheet(f"background: {Colors.BG_CARD}; color: {Colors.TEXT_PRIMARY};")
        lay = QVBoxLayout(dlg)

        pw1 = QLineEdit()
        pw1.setEchoMode(QLineEdit.EchoMode.Password)
        pw1.setPlaceholderText("Decoy Password")

        pw2 = QLineEdit()
        pw2.setEchoMode(QLineEdit.EchoMode.Password)
        pw2.setPlaceholderText("Confirm Decoy Password")

        lay.addWidget(pw1)
        lay.addWidget(pw2)

        btn = GlowButton("Save Decoy Password")
        active_worker: list[Optional[_SettingsWorker]] = [None]

        def _set_busy(busy: bool) -> None:
            for w in (pw1, pw2, btn):
                w.setEnabled(not busy)
            btn.setText("Saving Decoy…" if busy else "Save Decoy Password")

        def _do_save():
            if active_worker[0] is not None:
                return
            if pw1.text() != pw2.text() or not pw1.text():
                QMessageBox.warning(dlg, "Error", "Passwords must match and not be empty.")
                return
            decoy_pw = pw1.text()
            _set_busy(True)

            worker = _SettingsWorker(lambda _cb: self.keystore.setup_decoy(decoy_pw), dlg)
            active_worker[0] = worker

            def _on_ok(_res: object) -> None:
                active_worker[0] = None
                worker.deleteLater()
                _set_busy(False)
                QMessageBox.information(dlg, "Decoy Active", "Decoy password configured. Logging in with it opens a fake safe.")
                dlg.accept()

            def _on_err(_msg: str) -> None:
                active_worker[0] = None
                worker.deleteLater()
                _set_busy(False)
                QMessageBox.critical(dlg, "Error", "Could not setup decoy password. Please try again. [ERR-DEC-01]")

            worker.finished_result.connect(_on_ok, Qt.ConnectionType.QueuedConnection)
            worker.failed.connect(_on_err, Qt.ConnectionType.QueuedConnection)
            worker.start()

        dlg.rejected.connect(lambda: active_worker[0].wait(3000) if active_worker[0] else None)
        btn.clicked.connect(_do_save)
        lay.addWidget(btn)
        dlg.exec()

    def _optimize_unlock_speed_dialog(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Optimize Unlock Speed")
        dlg.setFixedSize(440, 260)
        dlg.setStyleSheet(f"background: {Colors.BG_CARD}; color: {Colors.TEXT_PRIMARY};")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(10)

        info = QLabel(
            "Re-tunes Argon2id key derivation to unlock in ~0.25 seconds on this CPU "
            "using OWASP-safe strength (>=64 MiB).\n\n"
            "Note: If you use a Decoy Password, re-save it in Settings afterward so both login paths stay fast."
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 12px; background: transparent;")
        lay.addWidget(info)

        pw_edit = QLineEdit()
        pw_edit.setEchoMode(QLineEdit.EchoMode.Password)
        pw_edit.setPlaceholderText("Enter current Master Password to confirm")
        lay.addWidget(pw_edit)

        btn = GlowButton("Optimize Speed Now")
        active_worker: list[Optional[_SettingsWorker]] = [None]

        def _set_busy(busy: bool) -> None:
            pw_edit.setEnabled(not busy)
            btn.setEnabled(not busy)
            btn.setText("Benchmarking & Re-tuning…" if busy else "Optimize Speed Now")

        def _do_optimize() -> None:
            if active_worker[0] is not None:
                return
            pw_val = pw_edit.text()
            if not pw_val:
                QMessageBox.warning(dlg, "Password Required", "Please enter your current master password.")
                return
            kf_path: Optional[str] = None
            try:
                if self.keystore.config.exists():
                    self.keystore.config.load()
                    if self.keystore.config.keyfile_enabled:
                        kf_path, _ = QFileDialog.getOpenFileName(dlg, "Select Current USB Key File")
                        if not kf_path:
                            return
            except Exception:
                QMessageBox.critical(dlg, "Error", "Could not read keystore configuration. [ERR-OPT-01]")
                return

            _set_busy(True)

            def _work(_cb):
                new_params = self.keystore.optimize_unlock_speed(pw_val, keyfile_path=kf_path)
                sk = make_lockout_signing_key(new_params.salt)
                return new_params, sk

            worker = _SettingsWorker(_work, dlg)
            active_worker[0] = worker

            def _on_ok(res: object) -> None:
                active_worker[0] = None
                worker.deleteLater()
                _set_busy(False)
                new_params, sk = res
                self.settings.rekey(sk)
                LockoutManager(self.vault.vault_dir, sk).rekey(sk, reset_failures=True)
                self.credentials_changed.emit()
                QMessageBox.information(
                    dlg,
                    "Unlock Speed Optimized",
                    f"Vault key derivation updated cleanly (~0.25s target).\n"
                    f"Memory: {new_params.memory_kib // 1024} MiB  •  Iterations: {new_params.time_cost}",
                )
                dlg.accept()

            def _on_err(_msg: str) -> None:
                active_worker[0] = None
                worker.deleteLater()
                _set_busy(False)
                QMessageBox.critical(
                    dlg,
                    "Optimization Failed",
                    "Incorrect master password or key file. No settings were changed. [ERR-OPT-02]",
                )

            worker.finished_result.connect(_on_ok, Qt.ConnectionType.QueuedConnection)
            worker.failed.connect(_on_err, Qt.ConnectionType.QueuedConnection)
            worker.start()

        dlg.rejected.connect(lambda: active_worker[0].wait(3000) if active_worker[0] else None)
        btn.clicked.connect(_do_optimize)
        lay.addWidget(btn)
        dlg.exec()

    def _rotate_keys_dialog(self) -> None:
        pw, ok = QInputDialog.getText(
            self,
            "Rotate Master Key",
            "Enter current master password to re-encrypt all files with a fresh Master Key:",
            QLineEdit.EchoMode.Password,
        )
        if not (ok and pw):
            return

        kf_path: Optional[str] = None
        if self.keystore.config.exists():
            try:
                self.keystore.config.load()
                if self.keystore.config.keyfile_enabled:
                    kf_path, _ = QFileDialog.getOpenFileName(self, "Select Current USB Key File")
                    if not kf_path:
                        return
            except Exception:
                pass

        mk = get_session().get_master_key()
        vault_dir = self.vault.vault_dir
        rec_out: list[str] = []

        def _work(cb):
            cb(0, 1, "Verifying master password…")
            try:
                test_mk = self.keystore.unlock_with_password(pw, keyfile_path=kf_path)
                from ..core.crypto import zero_bytes
                zero_bytes(test_mk)
            except Exception:
                return ("auth_failed", None)

            return (
                "rotated",
                rotate_vault_keys(
                    vault_dir,
                    mk,
                    pw,
                    keyfile_path=kf_path,
                    progress=cb,
                    recovery_key_out=rec_out,
                ),
            )

        def _done(res):
            status, payload = res
            if status == "auth_failed":
                QMessageBox.critical(self, "Authentication Failed", "Incorrect master password or key file. [ERR-ROT-01]")
                return

            success, _msg = payload
            if success:
                try:
                    self.keystore.config.load()
                    sk = make_lockout_signing_key(self.keystore.config.kdf_params.salt)
                    self.settings.signing_key = sk
                    self.settings.load()
                    self.credentials_changed.emit()
                except Exception:
                    pass
                if rec_out:
                    self._show_rotated_recovery_dialog(rec_out[0])
                else:
                    QMessageBox.information(
                        self,
                        "Success",
                        "Vault Master Key rotated and all files re-encrypted.\nPlease lock and log in again.",
                    )
                self.lock_requested.emit()
            else:
                QMessageBox.critical(self, "Error", "Key rotation failed. Your original vault remains intact. [ERR-ROT-02]")

        self._run_with_overlay("Rotating Keys & Re-encrypting", _work, _done)

    def _show_rotated_recovery_dialog(self, recovery_key: str) -> None:
        from PySide6.QtWidgets import QApplication
        dlg = QDialog(self)
        dlg.setWindowTitle("Save Your New Recovery Key")
        dlg.setFixedSize(520, 290)
        dlg.setStyleSheet(f"background: {Colors.BG_CARD}; color: {Colors.TEXT_PRIMARY};")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)

        info = QLabel(
            "Your Vault Master Key has been rotated and all files were re-encrypted.\n"
            "Save this NEW 256-bit Recovery Key now (the old recovery key is no longer valid):"
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 12px; background: transparent;")
        lay.addWidget(info)

        key_lbl = QLabel(recovery_key)
        key_lbl.setWordWrap(True)
        key_lbl.setFont(QFont("Cascadia Code, Consolas, Courier New", 11, QFont.Weight.Bold))
        key_lbl.setStyleSheet(
            f"background: {Colors.BG_INPUT}; color: {Colors.ACCENT}; "
            f"border: 1px solid {Colors.BORDER}; border-radius: 8px; padding: 12px;"
        )
        lay.addWidget(key_lbl)

        btn_row = QHBoxLayout()
        copy_btn = QPushButton(" Copy Key")
        copy_btn.setIcon(icons.get_icon("copy", color=Colors.TEXT_PRIMARY))
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(recovery_key))

        save_btn = QPushButton(" Save to File…")
        save_btn.setIcon(icons.get_icon("download", color=Colors.TEXT_PRIMARY))

        def _save_to_file() -> None:
            path, _ = QFileDialog.getSaveFileName(
                dlg, "Save New Recovery Key", "PersonalVault-NewRecoveryKey.txt", "Text Files (*.txt)"
            )
            if path:
                try:
                    Path(path).write_text(
                        "Personal Vault Rotated Recovery Key\n"
                        "Store this somewhere safe outside your encrypted vault:\n\n"
                        f"{recovery_key}\n",
                        encoding="utf-8",
                    )
                except Exception:
                    QMessageBox.warning(dlg, "Save Error", "Could not save recovery key file. [ERR-ROT-03]")

        save_btn.clicked.connect(_save_to_file)

        done_btn = GlowButton(" Done & Lock Vault")
        done_btn.clicked.connect(dlg.accept)

        btn_row.addWidget(copy_btn)
        btn_row.addWidget(save_btn)
        btn_row.addStretch()
        btn_row.addWidget(done_btn)
        lay.addLayout(btn_row)
        dlg.exec()


    def _confirm_self_destruct_dialog(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Confirm Emergency Self-Destruct")
        dlg.setFixedSize(440, 260)
        dlg.setStyleSheet(f"background: {Colors.BG_CARD}; color: {Colors.TEXT_PRIMARY};")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(12)

        warn_lbl = QLabel(
            "⚠️ PERMANENT VAULT DESTRUCTION ⚠️\n\n"
            "This will permanently and irrecoverably overwrite all cryptographic key slots "
            "with random noise. All encrypted files will become unreadable forever.\n\n"
            "To confirm, type 'DESTROY MY VAULT' below:"
        )
        warn_lbl.setWordWrap(True)
        warn_lbl.setStyleSheet(f"color: {Colors.DANGER}; font-size: 12px; font-weight: bold;")
        lay.addWidget(warn_lbl)

        confirm_input = QLineEdit()
        confirm_input.setPlaceholderText("DESTROY MY VAULT")
        confirm_input.setStyleSheet(f"padding: 8px; border: 1px solid {Colors.DANGER}; border-radius: 6px;")
        lay.addWidget(confirm_input)

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dlg.reject)

        destroy_btn = QPushButton("Permanently Wipe Keys")
        destroy_btn.setStyleSheet(f"background-color: {Colors.DANGER}; color: {Colors.WHITE}; font-weight: bold; padding: 8px 16px; border-radius: 6px;")

        def _do_wipe():
            if confirm_input.text().strip() != "DESTROY MY VAULT":
                QMessageBox.warning(dlg, "Confirmation Failed", "You must type 'DESTROY MY VAULT' exactly to confirm.")
                return
            try:
                self.keystore.self_destruct()
                QMessageBox.critical(dlg, "Vault Destroyed", "All key material has been wiped. The vault is now permanently locked.")
                dlg.accept()
                self.lock_requested.emit()
            except Exception:
                QMessageBox.critical(dlg, "Error", "Self-destruct encountered an error. [ERR-DESTRUCT-01]")

        destroy_btn.clicked.connect(_do_wipe)
        btn_row.addStretch(1)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(destroy_btn)
        lay.addLayout(btn_row)

        dlg.exec()


__all__ = ["SettingsUI"]

