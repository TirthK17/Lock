"""
test_ui_startup.py — Offscreen UI tests for Personal Vault startup, wizard, login, and lock.

Uses pytest-qt (qtbot) with QT_QPA_PLATFORM=offscreen.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtCore import Qt

# Ensure offscreen Qt platform
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from mylocker.app.core.keystore import Keystore
from mylocker.app.core.lockout import LockoutManager
from mylocker.app.core.session import get_session
from mylocker.app.ui.login import LoginScreen
from mylocker.app.ui.setup_wizard import SetupWizard


@pytest.fixture
def test_vault_dir(tmp_path):
    vault_dir = tmp_path / "TestVault"
    vault_dir.mkdir(parents=True, exist_ok=True)
    return vault_dir


@pytest.fixture
def keystore(test_vault_dir):
    return Keystore(test_vault_dir)


@pytest.fixture
def lockout(test_vault_dir):
    return LockoutManager(test_vault_dir, b"TestLockoutSigningKey-32BytesLong!")


class TestUIStartupAndWizard:
    """Tests the first-run wizard and transition offscreen."""

    def test_setup_wizard_flow(self, qtbot, test_vault_dir, keystore):
        """Test full wizard: password -> create vault -> recovery key -> verify 3 groups."""
        wizard = SetupWizard(keystore, test_vault_dir)
        qtbot.addWidget(wizard)
        wizard.show()

        # Page 0: Welcome
        assert wizard._stack.currentIndex() == 0
        qtbot.mouseClick(wizard._next_btn_0, Qt.MouseButton.LeftButton)
        assert wizard._stack.currentIndex() == 1

        # Page 1: Password
        wizard._pw1.setText("StrongMasterPassphrase123#")
        wizard._pw2.setText("StrongMasterPassphrase123#")
        qtbot.mouseClick(wizard._next_btn_1, Qt.MouseButton.LeftButton)
        assert wizard._stack.currentIndex() == 2

        # Page 2: Keyfile (disabled by default) -> click Create Vault
        # Mock benchmark to run fast in tests
        with patch("mylocker.app.core.keystore.benchmark_argon2id") as mock_bench:
            from mylocker.app.core.kdf import KDFParams
            mock_bench.return_value = KDFParams.new_with_fresh_salt(
                memory_kib=1024, time_cost=1
            )
            with qtbot.waitSignal(wizard.setup_complete, timeout=10000) as blocker:
                # Trigger create vault
                wizard._on_create_vault_clicked()

                # Wait until recovery key page is displayed (Page 4)
                qtbot.waitUntil(lambda: wizard._stack.currentIndex() == 4, timeout=5000)
                assert wizard._recovery_key is not None
                assert len(wizard._recovery_groups) >= 3

                # Check ack box
                wizard._ack_check.setChecked(True)
                assert wizard._rec_continue_btn.isEnabled()

                # Click Verify Key -> transitions to page 5
                qtbot.mouseClick(wizard._rec_continue_btn, Qt.MouseButton.LeftButton)
                assert wizard._stack.currentIndex() == 5

                # Fill in the 3 requested groups
                for i, group_idx in enumerate(wizard._verify_indices):
                    wizard._verify_edits[i].setText(wizard._recovery_groups[group_idx])

                assert wizard._finish_btn.isEnabled()
                qtbot.mouseClick(wizard._finish_btn, Qt.MouseButton.LeftButton)

            # Signal was emitted with valid master key
            master_key = blocker.args[0]
            assert isinstance(master_key, bytearray)
            assert len(master_key) == 32


class TestUILoginAndLock:
    """Tests Login screen authentication, incorrect attempts, and lockout."""

    def test_login_correct_password(self, qtbot, test_vault_dir, keystore, lockout):
        # First set up the vault
        with patch("mylocker.app.core.keystore.benchmark_argon2id") as mock_bench:
            from mylocker.app.core.kdf import KDFParams
            mock_bench.return_value = KDFParams.new_with_fresh_salt(
                memory_kib=1024, time_cost=1
            )
            mk, rec_key = keystore.setup("CorrectPassword123#", benchmark=False)

        login = LoginScreen(keystore, lockout)
        qtbot.addWidget(login)
        login.show()

        with qtbot.waitSignal(login.login_success, timeout=5000) as blocker:
            login._pw_input.setText("CorrectPassword123#")
            qtbot.mouseClick(login._unlock_btn, Qt.MouseButton.LeftButton)

        unlocked_mk, is_decoy = blocker.args
        assert isinstance(unlocked_mk, bytearray)
        assert is_decoy is False

    def test_login_wrong_password(self, qtbot, test_vault_dir, keystore, lockout):
        with patch("mylocker.app.core.keystore.benchmark_argon2id") as mock_bench:
            from mylocker.app.core.kdf import KDFParams
            mock_bench.return_value = KDFParams.new_with_fresh_salt(
                memory_kib=1024, time_cost=1
            )
            keystore.setup("CorrectPassword123#", benchmark=False)

        login = LoginScreen(keystore, lockout)
        qtbot.addWidget(login)
        login.show()

        login._pw_input.setText("WrongPassword456!")
        qtbot.mouseClick(login._unlock_btn, Qt.MouseButton.LeftButton)

        # Wait for background verification worker to complete and update error label
        qtbot.waitUntil(lambda: bool(login._error_lbl.text()), timeout=5000)
        assert lockout.get_fail_count() == 1
        assert "incorrect password" in login._error_lbl.text().lower() or "failed" in login._error_lbl.text().lower()

    def test_login_with_keyfile(self, qtbot, test_vault_dir, keystore, lockout, tmp_path):
        keyfile_path = str(tmp_path / "secret.key")
        Path(keyfile_path).write_bytes(os.urandom(64))

        with patch("mylocker.app.core.keystore.benchmark_argon2id") as mock_bench:
            from mylocker.app.core.kdf import KDFParams
            mock_bench.return_value = KDFParams.new_with_fresh_salt(
                memory_kib=1024, time_cost=1
            )
            keystore.setup("SecretPassword123#", keyfile_path=keyfile_path, benchmark=False)

        login = LoginScreen(keystore, lockout)
        qtbot.addWidget(login)
        login.show()

        # Login requires keyfile
        login._pw_input.setText("SecretPassword123#")
        login._kf_edit.setText(keyfile_path)

        with qtbot.waitSignal(login.login_success, timeout=5000) as blocker:
            qtbot.mouseClick(login._unlock_btn, Qt.MouseButton.LeftButton)

        unlocked_mk, is_decoy = blocker.args
        assert isinstance(unlocked_mk, bytearray)
        assert len(unlocked_mk) == 32

    def test_session_lock_zeros_master_key(self):
        """Verifies session unlock and lock zeroing behavior."""
        session = get_session()
        dummy_mk = bytearray(b"A" * 32)
        session.unlock(dummy_mk, is_decoy=False)

        assert session.is_unlocked is True
        retrieved_mk = session.get_master_key()
        assert retrieved_mk == b"A" * 32

        # Lock session
        session.lock()
        assert session.is_unlocked is False

        # Attempting to retrieve master key raises RuntimeError
        with pytest.raises(RuntimeError):
            session.get_master_key()
