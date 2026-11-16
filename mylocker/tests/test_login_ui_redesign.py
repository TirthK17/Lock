"""
test_login_ui_redesign.py — Comprehensive UI, responsiveness, animation, security,
and lifecycle tests for the redesigned Personal Vault Login Screen.
"""

from __future__ import annotations

import logging
import os
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QAbstractAnimation, QEvent, Qt
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from mylocker.app.core.kdf import KDFParams
from mylocker.app.core.keystore import Keystore
from mylocker.app.core.lockout import LockoutManager
from mylocker.app.ui.login import LoginScreen, RecoveryKeyDialog


def _fast_kdf() -> KDFParams:
    return KDFParams.new_with_fresh_salt(memory_kib=1024, time_cost=1)


def _make_settings(reduced_motion: bool = False):
    store = MagicMock()
    store.get.side_effect = lambda key, default=None: reduced_motion if key == "reduced_motion" else default
    return store


class TestLoginUIRedesignBehavior:
    def test_visibility_toggle_and_focus_order(self, qtbot, tmp_path):
        vault_dir = tmp_path / "VisVault"
        ks = Keystore(vault_dir)
        ks.setup("StrongPass#2026!", benchmark=False, kdf_params=_fast_kdf())
        lm = LockoutManager(vault_dir, b"TestLockoutKey-32BytesLong-123456!")

        login = LoginScreen(ks, lm, settings_store=_make_settings(False))
        qtbot.addWidget(login)
        login.show()

        try:
            # 1. Password field defaults to hidden echo mode
            login._pw_input.setText("MySecretPass#9")
            assert login._pw_input.echoMode() == QLineEdit.EchoMode.Password

            # Eye toggle reveals then masks without clearing text
            qtbot.mouseClick(login._pw_field._eye_btn, Qt.MouseButton.LeftButton)
            assert login._pw_input.echoMode() == QLineEdit.EchoMode.Normal
            assert login._pw_input.text() == "MySecretPass#9"

            login._toggle_password_visibility()
            assert login._pw_input.echoMode() == QLineEdit.EchoMode.Password
            assert login._pw_input.text() == "MySecretPass#9"

            # 2. Tab order follows logical keyboard sequence
            w1 = login._pw_input.nextInFocusChain()
            assert w1 is login._kf_widget.browse_btn
            w2 = w1.nextInFocusChain()
            assert w2 is login._unlock_btn
            w3 = w2.nextInFocusChain()
            assert w3 is login._recovery_btn
            w4 = w3.nextInFocusChain()
            assert w4 is login._switch_btn
        finally:
            login.cleanup()

    def test_keyfile_selector_states_and_browse(self, qtbot, tmp_path):
        vault_dir = tmp_path / "KfStateVault"
        ks = Keystore(vault_dir)
        valid_kf = tmp_path / "vault.key"
        valid_kf.write_bytes(b"0123456789abcdef" * 4)
        ks.setup("KeyfileSecret#26", keyfile_path=str(valid_kf), benchmark=False, kdf_params=_fast_kdf())
        lm = LockoutManager(vault_dir, b"TestLockoutKey-32BytesLong-123456!")

        login = LoginScreen(ks, lm, settings_store=_make_settings(False))
        qtbot.addWidget(login)
        login.show()

        try:
            # Empty required state
            assert "no key file selected" in login._kf_widget._status_lbl.text().lower()

            # Non-existent path shows inline error state
            missing_kf = tmp_path / "missing.key"
            login._kf_edit.setText(str(missing_kf))
            assert login._kf_widget._state == "error"
            assert "unable to read key file" in login._kf_widget._status_lbl.text().lower()

            # Browse dialog selects readable key file -> Ready state
            with patch("mylocker.app.ui.login.QFileDialog.getOpenFileName", return_value=(str(valid_kf), "")):
                qtbot.mouseClick(login._kf_widget.browse_btn, Qt.MouseButton.LeftButton)

            assert login._kf_edit.text() == str(valid_kf)
            assert login._kf_widget._state == "ready"
            assert "key file ready" in login._kf_widget._status_lbl.text().lower()
        finally:
            login.cleanup()

    def test_enter_key_unlocks_and_never_logs_password(self, qtbot, tmp_path, caplog):
        vault_dir = tmp_path / "EnterVault"
        ks = Keystore(vault_dir)
        secret = "SuperSensitivePw!#42"
        ks.setup(secret, benchmark=False, kdf_params=_fast_kdf())
        lm = LockoutManager(vault_dir, b"TestLockoutKey-32BytesLong-123456!")

        login = LoginScreen(ks, lm, settings_store=_make_settings(False))
        qtbot.addWidget(login)
        login.show()

        try:
            with caplog.at_level(logging.DEBUG):
                login._pw_input.setText(secret)
                with qtbot.waitSignal(login.login_success, timeout=5000) as blocker:
                    qtbot.keyClick(login._pw_input, Qt.Key.Key_Return)

            mk, is_decoy = blocker.args
            assert isinstance(mk, bytearray) and len(mk) == 32
            assert is_decoy is False
            # Password cleared cleanly after unlock
            assert login._pw_input.text() == ""
            # No password strings in any log message
            for rec in caplog.records:
                assert secret not in rec.getMessage()
        finally:
            login.cleanup()

    def test_incorrect_password_visual_error_and_lockout_countdown(self, qtbot, tmp_path):
        vault_dir = tmp_path / "LockoutVault"
        ks = Keystore(vault_dir)
        ks.setup("CorrectPass#2026!", benchmark=False, kdf_params=_fast_kdf())
        lm = LockoutManager(vault_dir, b"TestLockoutKey-32BytesLong-123456!")

        login = LoginScreen(ks, lm, settings_store=_make_settings(False))
        qtbot.addWidget(login)
        login.show()

        try:
            # 1. Wrong password triggers inline error & field error highlight
            login._pw_input.setText("WrongPass#99")
            qtbot.mouseClick(login._unlock_btn, Qt.MouseButton.LeftButton)
            qtbot.waitUntil(lambda: login._error_lbl.isVisible(), timeout=5000)

            assert login._pw_field._error_state is True
            assert "incorrect password" in login._error_lbl.text().lower()
            assert login._unlock_btn.isEnabled() is True

            # Typing again clears error indicator cleanly
            login._pw_input.setText("N")
            assert login._pw_field._error_state is False
            assert login._error_lbl.isVisible() is False

            # 2. Active lockout disables controls and counts down
            lock_secs = {"rem": 12.0}
            with (
                patch.object(lm, "get_lockout_seconds", side_effect=lambda: lock_secs["rem"]),
                patch.object(lm, "check_lockout", side_effect=Exception("Too many failed attempts. Wait 12s.")),
            ):
                login._check_initial_lockout()
                assert login._unlock_btn.isEnabled() is False
                assert login._pw_input.isEnabled() is False
                assert "12s" in login._error_lbl.text()
                assert login._lockout_timer is not None and login._lockout_timer.isActive()

                # When remaining drops to 0, countdown tick re-enables UI
                lock_secs["rem"] = 0.0
                login._update_lockout_countdown()
                assert login._unlock_btn.isEnabled() is True
                assert login._pw_input.isEnabled() is True
                assert login._error_lbl.isVisible() is False
        finally:
            login.cleanup()

    def test_recovery_and_switch_signals_and_honest_status_badge(self, qtbot, tmp_path):
        # 1. Unconfigured vault must NOT falsely claim "Vault protection active"
        empty_vault = tmp_path / "EmptyVault"
        ks_empty = Keystore(empty_vault)
        login_empty = LoginScreen(ks_empty, None, settings_store=_make_settings(True))
        qtbot.addWidget(login_empty)
        login_empty.show()
        try:
            assert "active" not in login_empty._status_badge._text_lbl.text().lower()
            assert login_empty._status_badge._active is False

            with qtbot.waitSignal(login_empty.recovery_requested, timeout=1000):
                qtbot.mouseClick(login_empty._recovery_btn, Qt.MouseButton.LeftButton)
            with qtbot.waitSignal(login_empty.switch_vault_requested, timeout=1000):
                qtbot.mouseClick(login_empty._switch_btn, Qt.MouseButton.LeftButton)
        finally:
            login_empty.cleanup()

        # 2. Configured vault with lockout shows "Vault protection active"
        cfg_vault = tmp_path / "ConfiguredVault"
        ks_cfg = Keystore(cfg_vault)
        ks_cfg.setup("ConfiguredPass#26", benchmark=False, kdf_params=_fast_kdf())
        lm_cfg = LockoutManager(cfg_vault, b"TestLockoutKey-32BytesLong-123456!")
        login_cfg = LoginScreen(ks_cfg, lm_cfg, settings_store=_make_settings(True))
        qtbot.addWidget(login_cfg)
        login_cfg.show()
        try:
            assert login_cfg._status_badge._active is True
            assert login_cfg._status_badge._text_lbl.text() == "Vault protection active"
        finally:
            login_cfg.cleanup()

        # 3. RecoveryKeyDialog validates non-empty key and returns normalized uppercase key
        dlg = RecoveryKeyDialog()
        qtbot.addWidget(dlg)
        dlg.show()
        qtbot.mouseClick(dlg._submit_btn, Qt.MouseButton.LeftButton)
        assert dlg._error_lbl.text() != ""
        dlg._key_input.setText("abcd-efgh-ijkl-mnop")
        dlg._on_submit()
        assert dlg.result() == QDialog.DialogCode.Accepted
        assert dlg.get_recovery_key() == "ABCD-EFGH-IJKL-MNOP"


class TestLoginUIAnimationsAndResponsiveness:
    def test_reduced_motion_and_hide_cleanup_stop_all_timers(self, qtbot, tmp_path):
        vault_dir = tmp_path / "AnimVault"
        ks = Keystore(vault_dir)
        ks.setup("AnimPass#2026!", benchmark=False, kdf_params=_fast_kdf())
        lm = LockoutManager(vault_dir, b"TestLockoutKey-32BytesLong-123456!")

        # Reduced motion keeps backdrop & pulse timers inactive even while shown
        login_rm = LoginScreen(ks, lm, settings_store=_make_settings(True))
        qtbot.addWidget(login_rm)
        login_rm.show()
        try:
            assert login_rm._backdrop._drift_timer.isActive() is False
            assert login_rm._status_badge._pulse_timer.isActive() is False
        finally:
            login_rm.cleanup()

        # Standard motion activates timers while visible, and hideEvent/cleanup stops all cleanly
        login = LoginScreen(ks, lm, settings_store=_make_settings(False))
        qtbot.addWidget(login)
        login.show()
        assert login._backdrop._drift_timer.isActive() is True
        assert login._status_badge._pulse_timer.isActive() is True

        # Trigger focus/card/unlock animations then hide
        QApplication.sendEvent(login._pw_input, QEvent(QEvent.Type.FocusIn))
        login._card.trigger_shake(reduced_motion=False)
        login._unlock_btn.set_loading(True)
        assert login._unlock_btn._spin_timer.isActive() is True

        login.hide()
        assert login._backdrop._drift_timer.isActive() is False
        assert login._status_badge._pulse_timer.isActive() is False
        assert login._unlock_btn._spin_timer.isActive() is False
        assert login._card._shake_anim.state() == QAbstractAnimation.State.Stopped
        assert login._pw_field._focus_anim.state() == QAbstractAnimation.State.Stopped

        login.cleanup()

    @pytest.mark.parametrize(
        "width,height",
        [
            (1280, 720),
            (1920, 1080),
            (2560, 1440),
            (3840, 2160),
            (600, 560),
        ],
    )
    def test_responsive_layout_no_clipping_across_resolutions(self, qtbot, tmp_path, width, height):
        vault_dir = tmp_path / "RespVault"
        ks = Keystore(vault_dir)
        ks.setup("ResponsivePass#26", benchmark=False, kdf_params=_fast_kdf())
        lm = LockoutManager(vault_dir, b"TestLockoutKey-32BytesLong-123456!")

        login = LoginScreen(ks, lm, settings_store=_make_settings(True))
        qtbot.addWidget(login)
        login.resize(width, height)
        login.show()
        QApplication.processEvents()

        try:
            # Backdrop matches full widget rect
            assert login._backdrop.geometry() == login.rect()

            # Card stays bounded cleanly within min/max limits and inside viewport width
            card_w = login._card.width()
            assert 360 <= card_w <= 444
            assert card_w <= width

            # All key controls remain visible, non-zero sized, and vertically ordered cleanly
            pw_y = login._pw_field.mapTo(login._card, login._pw_field.rect().topLeft()).y()
            kf_y = login._kf_widget.mapTo(login._card, login._kf_widget.rect().topLeft()).y()
            btn_y = login._unlock_btn.mapTo(login._card, login._unlock_btn.rect().topLeft()).y()
            rec_y = login._recovery_btn.mapTo(login._card, login._recovery_btn.rect().topLeft()).y()

            assert pw_y < kf_y < btn_y < rec_y
            assert login._pw_field.height() >= 54
            assert login._kf_widget.height() >= 52
            assert login._unlock_btn.height() >= 56
            assert login._status_badge.height() >= 24

            # At 1280x720+, entire stage fits cleanly without vertical scrolling
            if height >= 720:
                assert login._scroll.verticalScrollBar().maximum() == 0
        finally:
            login.cleanup()
