"""
test_end_to_end_fixes.py — Comprehensive regression tests for end-to-end vault flows,
Forgot Password / Recovery Key reset, salt rekeying, document opening, key rotation, and offscreen UI.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtCore import Qt

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from mylocker.app.applock.config import AppLockConfig
from mylocker.app.core.backup import rotate_vault_keys
from mylocker.app.core.crypto import CryptoError, zero_bytes
from mylocker.app.core.kdf import KDFParams
from mylocker.app.core.keystore import Keystore
from mylocker.app.core.lockout import LockoutManager, make_lockout_signing_key
from mylocker.app.core.session import get_session
from mylocker.app.core.settings_store import SettingsStore
from mylocker.app.core.vault import Vault
from mylocker.app.system.external_opener import open_external_securely
from mylocker.app.system.lock_controller import get_lock_controller
from mylocker.app.ui.login import ForgotPasswordDialog, LoginScreen


def _fast_kdf() -> KDFParams:
    return KDFParams.new_with_fresh_salt(memory_kib=1024, time_cost=1)


@pytest.fixture(autouse=True)
def _clean_session():
    get_session().lock()
    yield
    get_session().lock()


class TestPasswordResetAndRekey:
    """Tests Forgot Password recovery key reset and auxiliary store rekeying."""

    def test_reset_password_with_recovery_key_and_decoy_preserved(self, tmp_path):
        vault_dir = tmp_path / "Vault"
        ks = Keystore(vault_dir)
        mk, rec_key = ks.setup(
            "InitialPass#2026A",
            benchmark=False,
            kdf_params=_fast_kdf(),
        )
        zero_bytes(mk)
        ks.setup_decoy("DecoyPass#2026B")

        # Wrong recovery key must raise CryptoError
        with pytest.raises(CryptoError):
            ks.reset_password_with_recovery_key("AAAA-BBBB-CCCC-DDDD", "NewStrongPass#2026C")

        # Weak password must raise ValueError without touching config
        with pytest.raises(ValueError):
            ks.reset_password_with_recovery_key(rec_key, "weak")

        # Reset with valid recovery key and strong new password
        new_mk = ks.reset_password_with_recovery_key(rec_key, "NewStrongPass#2026C")
        assert isinstance(new_mk, bytearray) and len(new_mk) == 32
        zero_bytes(new_mk)

        # Old password must no longer work
        with pytest.raises(CryptoError):
            ks.unlock_with_password("InitialPass#2026A")

        # New password works
        mk2 = ks.unlock_with_password("NewStrongPass#2026C")
        assert len(mk2) == 32
        zero_bytes(mk2)

        # Recovery key still works
        mk3 = ks.unlock_with_recovery_key(rec_key)
        assert len(mk3) == 32
        zero_bytes(mk3)

        # Decoy still works
        dmk = ks.unlock_decoy("DecoyPass#2026B")
        assert len(dmk) == 32
        zero_bytes(dmk)

    def test_change_password_and_reset_rekey_preserves_settings_and_lockout(self, tmp_path):
        vault_dir = tmp_path / "Vault"
        ks = Keystore(vault_dir)
        mk, rec_key = ks.setup("OldStrongPass#2026", benchmark=False, kdf_params=_fast_kdf())
        zero_bytes(mk)

        sk1 = make_lockout_signing_key(ks.config.kdf_params.salt)
        settings = SettingsStore(vault_dir, sk1)
        settings.set("idle_timeout_minutes", 17)
        settings.set("calculator_disguise_enabled", True)

        lockout = LockoutManager(vault_dir, sk1)
        lockout.record_failure()
        assert lockout.get_fail_count() == 1

        # Change password changes salt
        ks.change_password("OldStrongPass#2026", "NewerStrong#2026!")
        ks.config.load()
        sk2 = make_lockout_signing_key(ks.config.kdf_params.salt)

        settings.rekey(sk2)
        lockout.rekey(sk2, reset_failures=True)

        # Reload with fresh instances using the new salt's signing key
        reloaded_settings = SettingsStore(vault_dir, sk2)
        assert reloaded_settings.get("idle_timeout_minutes") == 17
        assert reloaded_settings.get("calculator_disguise_enabled") is True

        reloaded_lockout = LockoutManager(vault_dir, sk2)
        reloaded_lockout.check_lockout()  # Must NOT trigger 3600s tamper lockout
        assert reloaded_lockout.get_fail_count() == 0

    def test_rotate_vault_keys_preserves_settings_decoy_applock_and_outputs_recovery(self, tmp_path):
        vault_dir = tmp_path / "Vault"
        ks = Keystore(vault_dir)
        mk, old_rec = ks.setup("RotateMaster#2026!", benchmark=False, kdf_params=_fast_kdf())
        ks.setup_decoy("DecoySecret#2026!")

        sk1 = make_lockout_signing_key(ks.config.kdf_params.salt)
        settings = SettingsStore(vault_dir, sk1)
        settings.set("idle_timeout_minutes", 25)
        lockout = LockoutManager(vault_dir, sk1)
        lockout.record_failure()

        applock = AppLockConfig(vault_dir)
        applock.add_locked_app("notepad.exe")

        get_session().unlock(bytearray(mk), is_decoy=False)
        vault = Vault(vault_dir)
        vault.initialise()
        src = tmp_path / "secret_note.txt"
        src.write_bytes(b"Confidential content 12345")
        vault.add_file(src)

        rec_out: list[str] = []
        ok, msg = rotate_vault_keys(
            vault_dir,
            mk,
            "RotateMaster#2026!",
            recovery_key_out=rec_out,
        )
        zero_bytes(mk)
        assert ok is True, msg
        assert len(rec_out) == 1
        new_rec = rec_out[0]
        assert new_rec != old_rec

        # Old session locked / re-unlock with rotated key
        get_session().lock()
        ks2 = Keystore(vault_dir)
        new_mk = ks2.unlock_with_password("RotateMaster#2026!")
        get_session().unlock(new_mk, is_decoy=False)

        # Verify file data decrypts under new MK (rotation assigns fresh file_id)
        vault2 = Vault(vault_dir)
        rotated_entries = vault2.load().all_files()
        assert len(rotated_entries) == 1
        assert rotated_entries[0].original_name == "secret_note.txt"
        assert vault2.open_file_in_memory(rotated_entries[0].file_id) == bytearray(b"Confidential content 12345")

        # Verify new recovery key works and old one fails
        with pytest.raises(CryptoError):
            ks2.unlock_with_recovery_key(old_rec)
        mk_rec = ks2.unlock_with_recovery_key(new_rec)
        zero_bytes(mk_rec)

        # Verify decoy password preserved
        dmk = ks2.unlock_decoy("DecoySecret#2026!")
        zero_bytes(dmk)

        # Verify auxiliary stores survived rotation with valid signatures under new salt
        sk2 = make_lockout_signing_key(ks2.config.kdf_params.salt)
        assert SettingsStore(vault_dir, sk2).get("idle_timeout_minutes") == 25
        LockoutManager(vault_dir, sk2).check_lockout()
        assert "notepad.exe" in AppLockConfig(vault_dir).get_locked_apps()


class TestExternalOpenerAndExport:
    """Tests Vault.export_file and open_external_securely for documents."""

    def test_export_file_and_open_external_securely(self, tmp_path):
        vault_dir = tmp_path / "Vault"
        ks = Keystore(vault_dir)
        mk, _ = ks.setup("ExportPass#2026!", benchmark=False, kdf_params=_fast_kdf())
        get_session().unlock(mk, is_decoy=False)

        vault = Vault(vault_dir)
        vault.initialise()
        doc = tmp_path / "report.txt"
        doc.write_bytes(b"Quarterly private report payload.")
        entry = vault.add_file(doc)

        # Direct export_file check
        out_path = tmp_path / "exported_report.txt"
        vault.export_file(entry.file_id, out_path)
        assert out_path.read_bytes() == b"Quarterly private report payload."

        # open_external_securely should succeed without AttributeError
        lc = get_lock_controller()
        before_dirs = set(lc._temp_dirs)
        with (
            patch("ctypes.windll.shell32.ShellExecuteExW", return_value=1) as mock_shell,
            patch("mylocker.app.system.external_opener.threading.Thread") as mock_thread,
        ):
            ok = open_external_securely(vault, entry.file_id)
            assert ok is True
            assert mock_shell.called
            assert mock_thread.called
            new_dirs = lc._temp_dirs - before_dirs
            assert len(new_dirs) == 1
            temp_dir = next(iter(new_dirs))
            launched_path = temp_dir / "report.txt"
            assert launched_path.exists()
            assert launched_path.read_bytes() == b"Quarterly private report payload."

        # Locking wipes the temp dir cleanly
        lc.lock("test_cleanup")
        assert not launched_path.exists()
        assert not temp_dir.exists()


class TestForgotPasswordUI:
    """Offscreen UI tests for ForgotPasswordDialog and LoginScreen."""

    def test_forgot_password_dialog_resets_password_offscreen(self, qtbot, tmp_path):
        vault_dir = tmp_path / "Vault"
        ks = Keystore(vault_dir)
        mk, rec_key = ks.setup("OrigStrongPass#2026", benchmark=False, kdf_params=_fast_kdf())
        zero_bytes(mk)
        sk = make_lockout_signing_key(ks.config.kdf_params.salt)
        lockout = LockoutManager(vault_dir, sk)

        login = LoginScreen(ks, lockout)
        qtbot.addWidget(login)
        login.show()

        dlg = ForgotPasswordDialog(ks, lockout, parent=login)
        qtbot.addWidget(dlg)
        dlg.show()

        # 1. Weak/mismatched new password does NOT burn lockout
        dlg._key_input.setText(rec_key)
        dlg._new_pw1.setText("short")
        dlg._new_pw2.setText("short")
        qtbot.mouseClick(dlg._submit_btn, Qt.MouseButton.LeftButton)
        assert lockout.get_fail_count() == 0
        assert dlg._error_lbl.text()

        # 2. Invalid recovery key increments lockout & shows [ERR-REC-01]
        dlg._key_input.setText("AAAA-BBBB-CCCC-DDDD-EEEE-FFFF-GGGG-HHHH")
        dlg._new_pw1.setText("ResetStrong#2026!")
        dlg._new_pw2.setText("ResetStrong#2026!")
        qtbot.mouseClick(dlg._submit_btn, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: "ERR-REC-01" in dlg._error_lbl.text(), timeout=5000)
        assert lockout.get_fail_count() == 1

        # 3. Valid recovery key resets password and emits accepted result
        dlg._key_input.setText(rec_key)
        with qtbot.waitSignal(dlg.accepted, timeout=5000):
            qtbot.mouseClick(dlg._submit_btn, Qt.MouseButton.LeftButton)

        res_mk, pw_reset = dlg.get_result()
        assert pw_reset is True
        assert isinstance(res_mk, bytearray) and len(res_mk) == 32
        zero_bytes(res_mk)
        assert lockout.get_fail_count() == 0

        # New password works in Keystore; old password fails
        with pytest.raises(CryptoError):
            ks.unlock_with_password("OrigStrongPass#2026")
        check_mk = ks.unlock_with_password("ResetStrong#2026!")
        zero_bytes(check_mk)


class TestEndToEndAppLifecycle:
    """Offscreen full PersonalVaultApp lifecycle: wizard -> custom dir -> dashboard -> lock -> forgot password."""

    def test_app_startup_wizard_custom_dir_lock_and_recovery_reset(self, qtbot, tmp_path):
        from PySide6.QtWidgets import QApplication

        import mylocker.main as app_mod

        fake_settings_file = tmp_path / "AppData" / "PersonalVaultSettings.json"
        fake_settings_file.parent.mkdir(parents=True, exist_ok=True)
        default_dir = tmp_path / "DefaultVault"
        custom_dir = tmp_path / "CustomVault"

        app = QApplication.instance()
        assert app is not None

        with (
            patch.object(app_mod, "_SETTINGS_PATH", fake_settings_file),
            patch.object(app_mod, "_load_vault_dir", return_value=default_dir),
            patch.object(app_mod, "hide_vault_directory"),
            patch.object(app_mod, "exclude_from_search_indexing"),
            patch.object(app_mod, "restrict_vault_acl"),
            patch("mylocker.app.ui.setup_wizard.SetupWizard._start_bitlocker_check_async"),
            patch("mylocker.app.system.tray.VaultTrayIcon.show"),
            patch("mylocker.app.system.hotkeys.PanicHotkeyManager.start"),
            patch("mylocker.app.core.keystore.benchmark_argon2id", return_value=_fast_kdf()),
        ):
            pv = app_mod.PersonalVaultApp(app)
            qtbot.addWidget(pv.stack)
            try:
                # Should start on SetupWizard cleanly
                assert pv._setup_wizard is not None
                assert pv.stack.currentWidget() is pv._setup_wizard

                # Choosing custom dir in wizard must NOT persist settings before completion
                pv._on_vault_location_chosen(custom_dir, persist=False)
                assert not fake_settings_file.exists()

                # Complete setup wizard steps
                wiz = pv._setup_wizard
                qtbot.mouseClick(wiz._next_btn_0, Qt.MouseButton.LeftButton)
                wiz._pw1.setText("E2EStrongPass#2026!")
                wiz._pw2.setText("E2EStrongPass#2026!")
                qtbot.mouseClick(wiz._next_btn_1, Qt.MouseButton.LeftButton)

                wiz._on_create_vault_clicked()
                qtbot.waitUntil(lambda: wiz._stack.currentIndex() == 4, timeout=5000)
                rec_key = wiz._recovery_key
                assert rec_key is not None

                wiz._ack_check.setChecked(True)
                qtbot.mouseClick(wiz._rec_continue_btn, Qt.MouseButton.LeftButton)
                for i, grp_idx in enumerate(wiz._verify_indices):
                    wiz._verify_edits[i].setText(wiz._recovery_groups[grp_idx])

                qtbot.mouseClick(wiz._finish_btn, Qt.MouseButton.LeftButton)
                qtbot.waitUntil(lambda: pv._dashboard is not None and get_session().is_unlocked, timeout=5000)

                # Settings file persisted ONLY now after completion
                assert fake_settings_file.exists()
                persisted = json.loads(fake_settings_file.read_text(encoding="utf-8"))
                assert Path(persisted["vault_dir"]).resolve() == custom_dir.resolve()

                # Add sample text file into vault and reload Dashboard grid
                sample_doc = tmp_path / "hello_e2e.txt"
                sample_doc.write_bytes(b"End-to-end encrypted document!")
                entry = pv.vault.add_file(sample_doc)
                pv._dashboard.load_files()
                assert len(pv._dashboard._current_entries) == 1

                # Lock vault -> Dashboard cleaned up, session locked, returned to LoginScreen
                pv.lock_vault("test")
                qtbot.waitUntil(lambda: pv._dashboard is None and not get_session().is_unlocked, timeout=5000)
                assert pv.stack.currentWidget() is pv.login_screen

                # Simulate recovery password reset via ForgotPasswordDialog + _rekey_stores + _on_login_success
                reset_mk = pv.keystore.reset_password_with_recovery_key(rec_key, "E2EResetPass#2026!")
                pv._rekey_stores()
                pv._on_login_success(reset_mk, is_decoy=False)

                assert get_session().is_unlocked
                assert pv._dashboard is not None
                assert pv.vault.open_file_in_memory(entry.file_id) == bytearray(b"End-to-end encrypted document!")
            finally:
                # Clean shutdown
                pv.lock_vault("quit")
                pv.lock_controller.stop_listeners()
                pv.tray.hide()
                pv._on_stack_destroyed()
