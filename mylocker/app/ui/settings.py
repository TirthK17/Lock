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
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core.backup import create_vault_backup, restore_vault_backup, rotate_vault_keys
from ..core.keystore import Keystore
from ..core.lockout import LockoutManager, make_lockout_signing_key
from ..core.password_policy import validate_password
from ..core.session import get_session
from .components import (
    Card,
    DangerButton,
    DialogShell,
    ListRow,
    PrimaryButton,
    SecondaryButton,
    SectionHeader,
    Toggle,
)
from .design_system import ThemePalette, Tokens
from .widgets import ProgressOverlay, SmoothScrollArea

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
    """Settings view embedded in the main dashboard (left category rail + right cards)."""

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
        self._section_cards: list[tuple[str, Card]] = []

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
        Tokens.subscribe(self._on_theme)
        self.destroyed.connect(self._cleanup_sub)
        self._apply_theme()
        self._load_stored_settings()

    def _cleanup_sub(self) -> None:
        Tokens.unsubscribe(self._on_theme)

    def _on_theme(self, _pal: ThemePalette) -> None:
        self._apply_theme()

    def _apply_theme(self) -> None:
        p = Tokens.colors
        self._header_lbl.setStyleSheet(f"color: {p.text}; background: transparent;")
        self._cat_list.setStyleSheet(f"""
            QListWidget {{
                background-color: {p.bg_secondary};
                border: 1px solid {p.border};
                border-radius: {Tokens.radius.CARD}px;
                padding: 6px;
                outline: none;
            }}
            QListWidget::item {{
                color: {p.text_secondary};
                padding: 8px 12px;
                border-radius: {Tokens.radius.CONTROL}px;
            }}
            QListWidget::item:hover {{
                background-color: {p.hover_fill};
                color: {p.text};
            }}
            QListWidget::item:selected {{
                background-color: {p.accent_tint};
                color: {p.text};
                border: 1px solid {p.accent_border};
            }}
        """)
        self._danger_card.setStyleSheet(f"""
            #DangerZoneCard {{
                background-color: {p.card};
                border: 1px solid {p.danger_border};
                border-radius: {Tokens.radius.CARD}px;
            }}
        """)
        self._destruct_desc.setStyleSheet(f"color: {p.danger}; background: transparent;")
        self._decoy_desc.setStyleSheet(f"color: {p.text_secondary}; background: transparent;")

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
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setSpacing(16)

        self._header_lbl = QLabel("Settings", self)
        self._header_lbl.setFont(Tokens.type.font(Tokens.type.PAGE_TITLE, Tokens.type.WEIGHT_TITLE))
        outer.addWidget(self._header_lbl)

        body_split = QHBoxLayout()
        body_split.setContentsMargins(0, 0, 0, 0)
        body_split.setSpacing(16)

        # Left Category Rail
        self._cat_list = QListWidget(self)
        self._cat_list.setFixedWidth(164)
        self._cat_list.setWordWrap(True)
        self._cat_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._cat_list.setAccessibleName("Settings categories")
        body_split.addWidget(self._cat_list, 0, Qt.AlignmentFlag.AlignTop)

        # Right Cards Scroll Area
        self._scroll = SmoothScrollArea()
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        container = QWidget()
        container.setMinimumWidth(0)
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(16)

        # ── 1. Security & Auto-Lock ──
        sec_card, sec_layout = self._create_card("Security & Auto-Lock")

        self.idle_spin = QSpinBox(sec_card)
        self.idle_spin.setRange(1, 120)
        self.idle_spin.setValue(5)
        self.idle_spin.setFixedWidth(76)
        self.idle_spin.setAccessibleName("Auto-lock inactivity timeout minutes")
        sec_layout.addWidget(
            ListRow(
                "Inactivity timeout",
                "Automatically lock the vault after N minutes with no input.",
                self.idle_spin,
                parent=sec_card,
            )
        )

        self.lock_minimize_check = Toggle(checked=True, parent=sec_card)
        self.lock_minimize_check.setAccessibleName("Lock vault when minimized")
        sec_layout.addWidget(
            ListRow(
                "Lock when minimized",
                "Zero keys and lock immediately when the main window minimizes.",
                self.lock_minimize_check,
                parent=sec_card,
            )
        )

        self.screen_protect_check = Toggle(checked=True, parent=sec_card)
        self.screen_protect_check.setAccessibleName("Block screenshots and screen recorders")
        sec_layout.addWidget(
            ListRow(
                "Screenshot protection",
                "Block captures and screen recording via Windows Display Affinity.",
                self.screen_protect_check,
                parent=sec_card,
            )
        )

        self.clipboard_wipe_check = Toggle(checked=True, parent=sec_card)
        self.clipboard_wipe_check.setAccessibleName("Clear clipboard after 30 seconds")
        sec_layout.addWidget(
            ListRow(
                "Auto-clear clipboard",
                "Erase sensitive copied text 30 seconds after copying.",
                self.clipboard_wipe_check,
                parent=sec_card,
            )
        )

        self.webcam_check = Toggle(checked=False, parent=sec_card)
        self.webcam_check.setAccessibleName("Capture webcam photo on failed login")
        sec_layout.addWidget(
            ListRow(
                "Intruder webcam capture",
                "Encrypt a webcam snapshot via DPAPI after failed unlock attempts.",
                self.webcam_check,
                parent=sec_card,
            )
        )
        layout.addWidget(sec_card)
        self._register_section("Security & Auto-Lock", sec_card)

        # ── 2. Stealth & Disguise ──
        stealth_card, stealth_layout = self._create_card("Stealth & Disguise")

        self.calc_check = Toggle(checked=False, parent=stealth_card)
        self.calc_check.setAccessibleName("Enable Calculator Disguise")
        stealth_layout.addWidget(
            ListRow(
                "Calculator disguise",
                "Show a working desktop calculator until the secret PIN + equals is entered.",
                self.calc_check,
                parent=stealth_card,
            )
        )

        self.pin_input = QLineEdit("1337", stealth_card)
        self.pin_input.setFixedWidth(100)
        self.pin_input.setAccessibleName("Calculator disguise secret PIN")
        stealth_layout.addWidget(
            ListRow(
                "Disguise secret PIN",
                "Sequence typed before pressing '=' on the calculator screen.",
                self.pin_input,
                parent=stealth_card,
            )
        )

        self.hide_taskbar_check = Toggle(checked=False, parent=stealth_card)
        self.hide_taskbar_check.setAccessibleName("Run silently in system tray only")
        stealth_layout.addWidget(
            ListRow(
                "Tray-only stealth mode",
                "Hide from the Windows taskbar when minimized.",
                self.hide_taskbar_check,
                parent=stealth_card,
            )
        )
        layout.addWidget(stealth_card)
        self._register_section("Stealth & Disguise", stealth_card)

        # ── 3. Decoy / Duress Vault ──
        decoy_card, decoy_layout = self._create_card("Decoy & Duress")
        self._decoy_desc = QLabel(
            "Configure a separate password that opens an empty decoy vault if forced to unlock under duress.",
            decoy_card,
        )
        self._decoy_desc.setFont(Tokens.type.font(Tokens.type.BODY, Tokens.type.WEIGHT_BODY))
        self._decoy_desc.setWordWrap(True)
        decoy_layout.addWidget(self._decoy_desc)

        setup_decoy_btn = SecondaryButton("Configure Decoy Password", icon_name="shield", parent=decoy_card)
        setup_decoy_btn.setMinimumWidth(180)
        setup_decoy_btn.clicked.connect(self._setup_decoy_dialog)
        decoy_layout.addWidget(setup_decoy_btn, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(decoy_card)
        self._register_section("Decoy & Duress", decoy_card)

        # ── 4. Password & Key Management ──
        key_card, key_layout = self._create_card("Keys & Speed")

        change_pw_btn = SecondaryButton("Change Master Password", icon_name="key", parent=key_card)
        change_pw_btn.clicked.connect(self._change_password_dialog)
        key_layout.addWidget(
            ListRow(
                "Master password",
                "Update your primary vault passphrase and re-wrap keystore credentials.",
                change_pw_btn,
                parent=key_card,
            )
        )

        rotate_key_btn = SecondaryButton("Rotate Master Key", icon_name="refresh", parent=key_card)
        rotate_key_btn.clicked.connect(self._rotate_keys_dialog)
        key_layout.addWidget(
            ListRow(
                "Rotate master key",
                "Generate a new 256-bit master key and re-encrypt every vault file cleanly.",
                rotate_key_btn,
                parent=key_card,
            )
        )

        optimize_speed_btn = SecondaryButton("Optimize (~0.25s)", icon_name="shield_check", parent=key_card)
        optimize_speed_btn.setToolTip(
            "Re-tunes Argon2id key derivation on this computer to unlock in ~0.25s "
            "while keeping OWASP-safe cryptographic strength (>=64 MiB)."
        )
        optimize_speed_btn.clicked.connect(self._optimize_unlock_speed_dialog)
        key_layout.addWidget(
            ListRow(
                "Unlock performance",
                "Benchmark Argon2id derivation on this CPU to hit ~0.25s OWASP-safe unlock.",
                optimize_speed_btn,
                parent=key_card,
            )
        )
        layout.addWidget(key_card)
        self._register_section("Keys & Speed", key_card)

        # ── 5. Backup & Verification ──
        backup_card, backup_layout = self._create_card("Backup & Integrity")

        export_btn = PrimaryButton("Export Backup", icon_name="download", parent=backup_card)
        export_btn.clicked.connect(self._export_backup)
        backup_layout.addWidget(
            ListRow(
                "Export encrypted backup",
                "Package your encrypted vault files and metadata into a portable archive.",
                export_btn,
                parent=backup_card,
            )
        )

        restore_btn = SecondaryButton("Restore Backup", icon_name="upload", parent=backup_card)
        restore_btn.clicked.connect(self._restore_backup)
        backup_layout.addWidget(
            ListRow(
                "Restore from backup",
                "Verify password and restore encrypted files from an existing archive.",
                restore_btn,
                parent=backup_card,
            )
        )

        verify_btn = SecondaryButton("Verify Integrity", icon_name="shield_check", parent=backup_card)
        verify_btn.clicked.connect(self._verify_vault)
        backup_layout.addWidget(
            ListRow(
                "Verify vault integrity",
                "Check all stored files against their AES-256-GCM authentication tags.",
                verify_btn,
                parent=backup_card,
            )
        )
        layout.addWidget(backup_card)
        self._register_section("Backup & Integrity", backup_card)

        # ── 6. Danger Zone Card (at the bottom) ──
        destruct_card = Card(container)
        destruct_card.setObjectName("DangerZoneCard")
        self._danger_card = destruct_card
        destruct_layout = QVBoxLayout(destruct_card)
        destruct_layout.setContentsMargins(16, 16, 16, 16)
        destruct_layout.setSpacing(12)
        destruct_layout.addWidget(SectionHeader("Danger Zone", destruct_card))

        self._destruct_desc = QLabel(
            "Permanently overwrite Master Key wrapping after 20 failed unlock attempts or immediately. "
            "Once wiped, encrypted vault data cannot be recovered.",
            destruct_card,
        )
        self._destruct_desc.setFont(Tokens.type.font(Tokens.type.BODY, Tokens.type.WEIGHT_BODY))
        self._destruct_desc.setWordWrap(True)
        destruct_layout.addWidget(self._destruct_desc)

        self.destruct_check = Toggle(checked=False, parent=destruct_card)
        self.destruct_check.setAccessibleName("Enable permanent self-destruct after failed attempts")
        destruct_layout.addWidget(
            ListRow(
                "Auto self-destruct",
                "Wipe key slots automatically after 20 consecutive failed unlock attempts.",
                self.destruct_check,
                parent=destruct_card,
            )
        )

        destruct_btn = DangerButton("Destroy Vault Now", icon_name="alert_triangle", parent=destruct_card)
        destruct_btn.setMinimumWidth(180)
        destruct_btn.clicked.connect(self._confirm_self_destruct_dialog)
        destruct_layout.addWidget(destruct_btn, 0, Qt.AlignmentFlag.AlignLeft)

        layout.addWidget(destruct_card)
        layout.addStretch(1)
        self._register_section("Danger Zone", destruct_card)

        self._cat_list.setCurrentRow(0)
        self._cat_list.currentRowChanged.connect(self._scroll_to_section)

        self._scroll.setWidget(container)
        body_split.addWidget(self._scroll, 1)
        outer.addLayout(body_split, 1)

    def _register_section(self, name: str, card: Card) -> None:
        self._section_cards.append((name, card))
        self._cat_list.addItem(QListWidgetItem(name))

    def _scroll_to_section(self, idx: int) -> None:
        if 0 <= idx < len(self._section_cards):
            card = self._section_cards[idx][1]
            self._scroll.ensureWidgetVisible(card, 0, 16)

    def _create_card(self, title: str) -> tuple[Card, QVBoxLayout]:
        card = Card(self)
        card.setObjectName("SettingsCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)
        lay.addWidget(SectionHeader(title, card))
        return card, lay

    # ── Actions ───────────────────────────────────────────────────────────────

    def _run_with_overlay(self, title: str, work_fn: Callable, on_done: Callable) -> None:
        overlay = ProgressOverlay(title, self)
        overlay._cancel_btn.hide()
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
            DialogShell.error(self, "Operation Failed", f"{msg} [ERR-SET-01]")

        worker.finished_result.connect(_finish)
        worker.failed.connect(_err)
        worker.start()

    def _on_destruct_toggled(self, checked: bool) -> None:
        if checked:
            confirmed = DialogShell.confirm(
                self,
                "Enable Auto Self-Destruct?",
                "If enabled, after 20 consecutive failed unlock attempts all master key slots "
                "will be permanently wiped and vault data will be UNRECOVERABLE.\n\nEnable this setting?",
                confirm_text="Enable Auto Wipe",
                danger=True,
            )
            if not confirmed:
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
                DialogShell.inform(self, "Backup Complete", "Encrypted backup created successfully.")
            else:
                DialogShell.error(
                    self,
                    "Backup Error",
                    "Failed to create encrypted backup. Please check destination permissions and try again. [ERR-BKP-01]",
                )

        self._run_with_overlay("Creating Encrypted Backup", _work, _done)

    def _restore_backup(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Backup Archive", "", "Tar Gzip Archive (*.tar.gz)"
        )
        if not file_path:
            return

        confirmed = DialogShell.confirm(
            self,
            "Confirm Restore",
            "Restoring a backup will replace your current vault data (a .old snapshot will be kept).\nAre you sure you want to proceed?",
            confirm_text="Continue",
            danger=True,
        )
        if not confirmed:
            return

        pw, pw_ok = DialogShell.get_text(
            self,
            "Verify Backup Password",
            "Enter the backup archive's master password to verify integrity before restoring:",
            echo_mode=QLineEdit.EchoMode.Password,
            confirm_text="Verify & Restore",
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
                DialogShell.inform(self, "Restore Succeeded", "Vault restored. Please lock and re-login.")
                self.lock_requested.emit()
            else:
                DialogShell.error(
                    self,
                    "Restore Error",
                    "Failed to restore backup. Check your password and archive integrity, then try again. [ERR-RST-01]",
                )

        self._run_with_overlay("Restoring Backup", _work, _done)

    def _verify_vault(self) -> None:
        def _work(cb):
            return self.vault.verify_all(progress=cb)

        def _done(results):
            total = len(results)
            failures = [fid for fid, (ok, _) in results.items() if not ok]
            if not failures:
                DialogShell.inform(self, "Integrity Check", f"All {total} files verified successfully! No corruption found.")
            else:
                DialogShell.warn(self, "Corruption Detected", f"{len(failures)} out of {total} files failed integrity verification!")

        self._run_with_overlay("Verifying Vault Integrity", _work, _done)

    def _change_password_dialog(self) -> None:
        dlg = DialogShell(
            "Change Master Password",
            "Re-wrap your vault keystore with a new strong password.",
            icon_name="key",
            parent=self,
            min_width=400,
        )

        old_pw = QLineEdit(dlg.body_widget)
        old_pw.setEchoMode(QLineEdit.EchoMode.Password)
        old_pw.setPlaceholderText("Current Password")

        new_pw = QLineEdit(dlg.body_widget)
        new_pw.setEchoMode(QLineEdit.EchoMode.Password)
        new_pw.setPlaceholderText("New Password (min 12 chars, mixed case + digit + symbol)")

        confirm_pw = QLineEdit(dlg.body_widget)
        confirm_pw.setEchoMode(QLineEdit.EchoMode.Password)
        confirm_pw.setPlaceholderText("Confirm New Password")

        dlg.body_layout.addWidget(old_pw)
        dlg.body_layout.addWidget(new_pw)
        dlg.body_layout.addWidget(confirm_pw)

        cancel_btn = SecondaryButton("Cancel", parent=dlg)
        cancel_btn.clicked.connect(dlg.reject)
        btn = PrimaryButton("Update Password", parent=dlg)
        dlg.add_footer_button(cancel_btn)
        dlg.add_footer_button(btn)

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
                DialogShell.warn(dlg, "Weak Password", pw_msg)
                return
            if new_pw.text() != confirm_pw.text():
                DialogShell.warn(dlg, "Error", "New passwords do not match.")
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
                DialogShell.error(dlg, "Error", "Could not read keystore configuration. [ERR-PW-01]")
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
                DialogShell.inform(dlg, "Success", "Master password updated successfully.")
                dlg.accept()

            def _on_err(_msg: str) -> None:
                active_worker[0] = None
                worker.deleteLater()
                _set_busy(False)
                DialogShell.error(dlg, "Error", "Failed to change password. Check current credentials and try again. [ERR-PW-01]")

            worker.finished_result.connect(_on_ok, Qt.ConnectionType.QueuedConnection)
            worker.failed.connect(_on_err, Qt.ConnectionType.QueuedConnection)
            worker.start()

        dlg.rejected.connect(lambda: active_worker[0].wait(3000) if active_worker[0] else None)
        btn.clicked.connect(_do_change)
        dlg.exec()

    def _setup_decoy_dialog(self) -> None:
        dlg = DialogShell(
            "Setup Decoy Password",
            "Create a separate passphrase that opens an empty decoy vault.",
            icon_name="shield",
            parent=self,
            min_width=380,
        )

        pw1 = QLineEdit(dlg.body_widget)
        pw1.setEchoMode(QLineEdit.EchoMode.Password)
        pw1.setPlaceholderText("Decoy Password")

        pw2 = QLineEdit(dlg.body_widget)
        pw2.setEchoMode(QLineEdit.EchoMode.Password)
        pw2.setPlaceholderText("Confirm Decoy Password")

        dlg.body_layout.addWidget(pw1)
        dlg.body_layout.addWidget(pw2)

        cancel_btn = SecondaryButton("Cancel", parent=dlg)
        cancel_btn.clicked.connect(dlg.reject)
        btn = PrimaryButton("Save Decoy Password", parent=dlg)
        dlg.add_footer_button(cancel_btn)
        dlg.add_footer_button(btn)

        active_worker: list[Optional[_SettingsWorker]] = [None]

        def _set_busy(busy: bool) -> None:
            for w in (pw1, pw2, btn):
                w.setEnabled(not busy)
            btn.setText("Saving Decoy…" if busy else "Save Decoy Password")

        def _do_save():
            if active_worker[0] is not None:
                return
            if pw1.text() != pw2.text() or not pw1.text():
                DialogShell.warn(dlg, "Error", "Passwords must match and not be empty.")
                return
            decoy_pw = pw1.text()
            _set_busy(True)

            worker = _SettingsWorker(lambda _cb: self.keystore.setup_decoy(decoy_pw), dlg)
            active_worker[0] = worker

            def _on_ok(_res: object) -> None:
                active_worker[0] = None
                worker.deleteLater()
                _set_busy(False)
                DialogShell.inform(dlg, "Decoy Active", "Decoy password configured. Logging in with it opens a fake safe.")
                dlg.accept()

            def _on_err(_msg: str) -> None:
                active_worker[0] = None
                worker.deleteLater()
                _set_busy(False)
                DialogShell.error(dlg, "Error", "Could not setup decoy password. Please try again. [ERR-DEC-01]")

            worker.finished_result.connect(_on_ok, Qt.ConnectionType.QueuedConnection)
            worker.failed.connect(_on_err, Qt.ConnectionType.QueuedConnection)
            worker.start()

        dlg.rejected.connect(lambda: active_worker[0].wait(3000) if active_worker[0] else None)
        btn.clicked.connect(_do_save)
        dlg.exec()

    def _optimize_unlock_speed_dialog(self) -> None:
        dlg = DialogShell(
            "Optimize Unlock Speed",
            "Re-tune Argon2id key derivation to ~0.25s on this CPU (>=64 MiB OWASP-safe).",
            icon_name="shield_check",
            icon_color=Tokens.colors.success,
            parent=self,
            min_width=420,
        )

        info = QLabel(
            "Note: If you use a Decoy Password, re-save it in Settings afterward so both login paths stay fast.",
            dlg.body_widget,
        )
        info.setWordWrap(True)
        info.setProperty("role", "secondary")
        dlg.body_layout.addWidget(info)

        pw_edit = QLineEdit(dlg.body_widget)
        pw_edit.setEchoMode(QLineEdit.EchoMode.Password)
        pw_edit.setPlaceholderText("Enter current Master Password to confirm")
        dlg.body_layout.addWidget(pw_edit)

        cancel_btn = SecondaryButton("Cancel", parent=dlg)
        cancel_btn.clicked.connect(dlg.reject)
        btn = PrimaryButton("Optimize Speed Now", parent=dlg)
        dlg.add_footer_button(cancel_btn)
        dlg.add_footer_button(btn)

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
                DialogShell.warn(dlg, "Password Required", "Please enter your current master password.")
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
                DialogShell.error(dlg, "Error", "Could not read keystore configuration. [ERR-OPT-01]")
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
                DialogShell.inform(
                    dlg,
                    "Unlock Speed Optimized",
                    f"Vault key derivation updated cleanly (~0.25s target).\n"
                    f"Memory: {new_params.memory_kib // 1024} MiB · Iterations: {new_params.time_cost}",
                )
                dlg.accept()

            def _on_err(_msg: str) -> None:
                active_worker[0] = None
                worker.deleteLater()
                _set_busy(False)
                DialogShell.error(
                    dlg,
                    "Optimization Failed",
                    "Incorrect master password or key file. No settings were changed. [ERR-OPT-02]",
                )

            worker.finished_result.connect(_on_ok, Qt.ConnectionType.QueuedConnection)
            worker.failed.connect(_on_err, Qt.ConnectionType.QueuedConnection)
            worker.start()

        dlg.rejected.connect(lambda: active_worker[0].wait(3000) if active_worker[0] else None)
        btn.clicked.connect(_do_optimize)
        dlg.exec()

    def _rotate_keys_dialog(self) -> None:
        pw, ok = DialogShell.get_text(
            self,
            "Rotate Master Key",
            "Enter current master password to re-encrypt all files with a fresh Master Key:",
            echo_mode=QLineEdit.EchoMode.Password,
            confirm_text="Rotate Keys",
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
                DialogShell.error(self, "Authentication Failed", "Incorrect master password or key file. [ERR-ROT-01]")
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
                    DialogShell.inform(
                        self,
                        "Success",
                        "Vault Master Key rotated and all files re-encrypted.\nPlease lock and log in again.",
                    )
                self.lock_requested.emit()
            else:
                DialogShell.error(self, "Error", "Key rotation failed. Your original vault remains intact. [ERR-ROT-02]")

        self._run_with_overlay("Rotating Keys & Re-encrypting", _work, _done)

    def _show_rotated_recovery_dialog(self, recovery_key: str) -> None:
        p = Tokens.colors
        dlg = DialogShell(
            "Save Your New Recovery Key",
            "Store this new 256-bit key safely; your previous recovery key is no longer valid.",
            icon_name="key",
            parent=self,
            min_width=480,
        )

        key_lbl = QLabel(recovery_key, dlg.body_widget)
        key_lbl.setWordWrap(True)
        key_lbl.setFont(QFont("Cascadia Code, Consolas, Courier New", 11, QFont.Weight.Bold))
        key_lbl.setStyleSheet(
            f"background-color: {p.input_bg}; color: {p.accent}; "
            f"border: 1px solid {p.border}; border-radius: {Tokens.radius.CONTROL}px; padding: 12px;"
        )
        dlg.body_layout.addWidget(key_lbl)

        copy_btn = SecondaryButton("Copy Key", icon_name="copy", parent=dlg)
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(recovery_key))

        save_btn = SecondaryButton("Save to File…", icon_name="download", parent=dlg)

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
                    DialogShell.warn(dlg, "Save Error", "Could not save recovery key file. [ERR-ROT-03]")

        save_btn.clicked.connect(_save_to_file)

        done_btn = PrimaryButton("Done & Lock Vault", parent=dlg)
        done_btn.clicked.connect(dlg.accept)

        dlg.add_footer_button(copy_btn)
        dlg.add_footer_button(save_btn)
        dlg.add_footer_button(done_btn)
        dlg.exec()

    def _confirm_self_destruct_dialog(self) -> None:
        p = Tokens.colors
        dlg = DialogShell(
            "Confirm Emergency Self-Destruct",
            "All encrypted files will become permanently unreadable.",
            icon_name="alert_triangle",
            icon_color=p.danger,
            parent=self,
            min_width=420,
        )

        warn_lbl = QLabel(
            "This permanently overwrites all cryptographic key slots with random noise.\n\n"
            "To confirm, type 'DESTROY MY VAULT' below:",
            dlg.body_widget,
        )
        warn_lbl.setWordWrap(True)
        warn_lbl.setStyleSheet(f"color: {p.danger}; font-weight: 500;")
        dlg.body_layout.addWidget(warn_lbl)

        confirm_input = QLineEdit(dlg.body_widget)
        confirm_input.setPlaceholderText("DESTROY MY VAULT")
        confirm_input.setAccessibleName("Self-destruct confirmation text")
        dlg.body_layout.addWidget(confirm_input)

        cancel_btn = SecondaryButton("Cancel", parent=dlg)
        cancel_btn.clicked.connect(dlg.reject)

        destroy_btn = DangerButton("Permanently Wipe Keys", parent=dlg)

        def _do_wipe():
            if confirm_input.text().strip() != "DESTROY MY VAULT":
                DialogShell.warn(dlg, "Confirmation Failed", "You must type 'DESTROY MY VAULT' exactly to confirm.")
                return
            try:
                self.keystore.self_destruct()
                DialogShell.error(dlg, "Vault Destroyed", "All key material has been wiped. The vault is now permanently locked.")
                dlg.accept()
                self.lock_requested.emit()
            except Exception:
                DialogShell.error(dlg, "Error", "Self-destruct encountered an error. [ERR-DESTRUCT-01]")

        destroy_btn.clicked.connect(_do_wipe)
        dlg.add_footer_button(cancel_btn)
        dlg.add_footer_button(destroy_btn)
        dlg.exec()


__all__ = ["SettingsUI"]

