"""
test_webcam_thread.py — Regression tests for moving intruder webcam capture off the GUI thread (B2).
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtCore import Qt

from mylocker.app.core.kdf import KDFParams
from mylocker.app.core.keystore import Keystore
from mylocker.app.core.lockout import LockoutManager, make_lockout_signing_key
from mylocker.app.core.session import get_session
from mylocker.app.system.webcam import IntruderPhotoCaptureThread
from mylocker.app.ui.login import ForgotPasswordDialog, LoginScreen


def _fast_kdf() -> KDFParams:
    return KDFParams.new_with_fresh_salt(memory_kib=1024, time_cost=1)


@pytest.fixture
def vault_env(tmp_path: Path):
    get_session().lock()
    vault_dir = tmp_path / "WebcamVault"
    ks = Keystore(vault_dir)
    kdf = _fast_kdf()
    mk, _ = ks.setup("CorrectPassword#1", benchmark=False, kdf_params=kdf)
    sk = make_lockout_signing_key(kdf.salt)
    lockout = LockoutManager(vault_dir, sk)
    yield vault_dir, ks, lockout
    get_session().lock()


def test_intruder_capture_thread_runs_and_emits(vault_env, qtbot):
    vault_dir, _, lockout = vault_env

    # Record initial failed attempt so attach_photo_to_latest has an entry
    lockout.record_failure()
    initial_log = lockout.get_intruder_log()
    assert len(initial_log) == 1
    assert initial_log[-1]["has_photo"] is False

    thread = IntruderPhotoCaptureThread(vault_dir, lockout=lockout)

    captured_paths = []
    thread.photo_captured.connect(captured_paths.append)

    with patch("mylocker.app.system.webcam.capture_intruder_photo", return_value=True) as mock_capture:
        thread.start()
        qtbot.waitUntil(lambda: len(captured_paths) > 0, timeout=3000)

        assert mock_capture.called
        rel_path = captured_paths[0]
        assert rel_path.startswith("intruder_photos/capture_")
        assert rel_path.endswith(".enc")

        # Verify photo was attached to intruder log entry
        updated_log = lockout.get_intruder_log()
        assert updated_log[-1]["has_photo"] is True
        assert updated_log[-1]["photo_file"] == rel_path


def test_intruder_capture_thread_handles_failure(vault_env, qtbot):
    vault_dir, _, lockout = vault_env
    thread = IntruderPhotoCaptureThread(vault_dir, lockout=lockout)

    captured_paths = []
    thread.photo_captured.connect(captured_paths.append)

    with patch("mylocker.app.system.webcam.capture_intruder_photo", return_value=False):
        thread.start()
        qtbot.waitUntil(lambda: len(captured_paths) > 0, timeout=3000)

        assert captured_paths[0] == ""


def test_login_screen_failed_attempt_spawns_thread_without_blocking_gui(vault_env, qtbot):
    vault_dir, ks, lockout = vault_env

    screen = LoginScreen(keystore=ks, lockout_manager=lockout)
    qtbot.addWidget(screen)
    screen.show()

    with patch("mylocker.app.system.webcam.capture_intruder_photo", return_value=True):
        # Enter wrong password
        screen._pw_input.setText("WrongPassword#99")
        qtbot.mouseClick(screen._unlock_btn, Qt.MouseButton.LeftButton)

        # Wait until error appears
        qtbot.waitUntil(lambda: screen._error_lbl.isVisible() and screen._error_lbl.text() != "", timeout=4000)

        # Confirm photo capture thread was created and tracked
        qtbot.waitUntil(lambda: len(screen._photo_threads) > 0 or lockout.get_intruder_log()[-1]["has_photo"] is True, timeout=3000)

    screen.cleanup()
    assert len(screen._photo_threads) == 0


def test_forgot_password_dialog_failed_attempt_spawns_thread(vault_env, qtbot):
    from mylocker.app.core.keystore import _format_recovery_key

    vault_dir, ks, lockout = vault_env

    dlg = ForgotPasswordDialog(keystore=ks, lockout=lockout)
    qtbot.addWidget(dlg)
    dlg.show()

    with patch("mylocker.app.system.webcam.capture_intruder_photo", return_value=True):
        # Uncheck reset mode so it simply validates the recovery key directly
        dlg._reset_check.setChecked(False)
        # Provide syntactically valid base32 key that is incorrect
        dlg._key_input.setText(_format_recovery_key(b"WRONG_KEY_DATA_32_BYTES_PADDING!"))
        qtbot.mouseClick(dlg._submit_btn, Qt.MouseButton.LeftButton)

        qtbot.waitUntil(lambda: dlg._error_lbl.text() != "", timeout=4000)
        qtbot.waitUntil(
            lambda: len(dlg._photo_threads) > 0 or (bool(lockout.get_intruder_log()) and lockout.get_intruder_log()[-1]["has_photo"] is True),
            timeout=3000,
        )

    dlg.close()
    assert len(dlg._photo_threads) == 0
