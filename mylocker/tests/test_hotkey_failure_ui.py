"""
test_hotkey_failure_ui.py — Tests for panic hotkey registration failure reporting.

Verifies:
  1. GlobalHotkeyThread invokes failure callback when RegisterHotKey fails.
  2. PanicHotkeyManager emits registration_failed signal and stores error.
  3. LockController captures and forwards hotkey failure signal and properties.
  4. Dashboard displays warning banner when hotkey registration fails dynamically.
  5. Dashboard displays warning banner on startup if hotkey was already failing.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mylocker.app.core.kdf import KDFParams
from mylocker.app.core.keystore import Keystore
from mylocker.app.core.session import get_session
from mylocker.app.core.vault import Vault
from mylocker.app.system.hotkeys import GlobalHotkeyThread, PanicHotkeyManager
from mylocker.app.system.lock_controller import LockController, get_lock_controller
from mylocker.app.ui.dashboard import Dashboard


def _fast_kdf() -> KDFParams:
    return KDFParams.new_with_fresh_salt(memory_kib=1024, time_cost=1)


@pytest.fixture
def initialized_vault(tmp_path: Path):
    get_session().lock()
    vault_dir = tmp_path / "test_vault"
    ks = Keystore(vault_dir)
    mk, _ = ks.setup("TestPass123!", benchmark=False, kdf_params=_fast_kdf())
    get_session().unlock(mk, is_decoy=False)

    v = Vault(vault_dir)
    v.initialise()
    yield v, vault_dir
    get_session().lock()


def test_global_hotkey_thread_failure_callback():
    callback_mock = MagicMock()
    failed_mock = MagicMock()

    thread = GlobalHotkeyThread(
        callback=callback_mock,
        on_registration_failed=failed_mock,
    )

    with (
        patch("ctypes.windll.user32.RegisterHotKey", return_value=0),
        patch("ctypes.windll.kernel32.GetLastError", return_value=1409),
    ):
        thread.run()

    failed_mock.assert_called_once_with(1409)
    assert thread.is_registered is False


def test_panic_hotkey_manager_emits_failure(qtbot):
    mgr = PanicHotkeyManager()
    signals = []
    mgr.registration_failed.connect(lambda code, msg: signals.append((code, msg)))

    mgr._on_failed(1409)

    assert len(signals) == 1
    code, msg = signals[0]
    assert code == 1409
    assert "Ctrl+Alt+Shift+L" in msg
    assert mgr.last_error == 1409
    assert mgr.is_registered is False


def test_lock_controller_forwards_hotkey_failure(qtbot):
    lc = LockController()
    emitted = []
    lc.hotkey_registration_failed.connect(lambda msg: emitted.append(msg))

    lc._on_hotkey_failed(1409, "Panic hotkey conflict test")

    assert len(emitted) == 1
    assert emitted[0] == "Panic hotkey conflict test"
    assert lc.hotkey_error_message == "Panic hotkey conflict test"
    assert lc.is_hotkey_registered is False


def test_dashboard_displays_hotkey_warning_banner(initialized_vault, qtbot, monkeypatch):
    vault, _ = initialized_vault
    lc = get_lock_controller()
    prev_err = lc._hotkey_error_message
    lc._hotkey_error_message = None
    lc._hotkey_manager.stop()
    monkeypatch.setattr("mylocker.app.system.hotkeys.GlobalHotkeyThread.start", lambda self: None)

    dash = Dashboard(vault)
    qtbot.addWidget(dash)
    dash._hotkey_banner.dismiss_immediately()
    qtbot.wait(50)

    try:
        # Initially banner is hidden
        assert dash._hotkey_banner.isHidden()

        # Trigger failure notification
        test_msg = "Global panic hotkey (Ctrl+Alt+Shift+L) conflict with another app."
        dash._on_hotkey_registration_failed(test_msg)

        # Banner should now be visible with the message
        assert not dash._hotkey_banner.isHidden()
        assert dash._hotkey_lbl.text() == test_msg
        assert "Panic hotkey inactive" in dash._protection_status.toolTip()
        assert test_msg in dash._notif_btn.toolTip()
    finally:
        dash.cleanup()
        lc._hotkey_error_message = prev_err


def test_dashboard_shows_banner_on_startup_if_hotkey_failed_previously(initialized_vault, qtbot, monkeypatch):
    vault, _ = initialized_vault
    lc = get_lock_controller()
    prev_err = lc._hotkey_error_message
    lc._hotkey_error_message = "Previous conflict error"
    monkeypatch.setattr("mylocker.app.system.hotkeys.GlobalHotkeyThread.start", lambda self: None)

    dash = Dashboard(vault)
    qtbot.addWidget(dash)
    qtbot.wait(50)

    try:
        assert not dash._hotkey_banner.isHidden()
        assert "Previous conflict error" in dash._hotkey_lbl.text()
    finally:
        dash.cleanup()
        lc._hotkey_error_message = prev_err
