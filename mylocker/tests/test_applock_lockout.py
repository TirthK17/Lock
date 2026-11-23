"""
test_applock_lockout.py — Tests for routing App Lock authentication attempts through LockoutManager.

Verifies:
  1. Successful App Lock unlock resets the shared lockout counter.
  2. Failed App Lock attempts increment the shared lockout counter and log intruder entries.
  3. Pre-existing active lockout disables inputs and terminates the intercepted app.
  4. Hitting lockout threshold triggers vault lock callback.
  5. Hitting 20 failures with self-destruct enabled triggers emergency keystore self-destruct wipe.
  6. Failed attempt spawns background intruder photo capture thread.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mylocker.app.applock.prompt import AppLockPrompt
from mylocker.app.core.kdf import KDFParams
from mylocker.app.core.keystore import Keystore
from mylocker.app.core.lockout import LockoutManager, make_lockout_signing_key
from mylocker.app.core.session import get_session
from mylocker.app.core.settings_store import SettingsStore
from mylocker.app.core.vault import Vault


def _fast_kdf() -> KDFParams:
    return KDFParams.new_with_fresh_salt(memory_kib=1024, time_cost=1)


@pytest.fixture
def lockout_setup(tmp_path: Path):
    get_session().lock()
    vault_dir = tmp_path / "test_vault"
    ks = Keystore(vault_dir)
    kdf_params = _fast_kdf()
    mk, _ = ks.setup("MasterPass123!", benchmark=False, kdf_params=kdf_params)
    get_session().unlock(mk, is_decoy=False)

    v = Vault(vault_dir)
    v.initialise()

    sk = make_lockout_signing_key(kdf_params.salt)
    lockout = LockoutManager(vault_dir, sk)
    settings = SettingsStore(vault_dir, sk, auto_create=True)

    yield ks, lockout, settings, vault_dir
    get_session().lock()


def test_applock_prompt_success_resets_lockout_counter(lockout_setup, qtbot):
    ks, lockout, settings, _ = lockout_setup
    lockout.record_failure()
    lockout.record_failure()
    assert lockout.get_fail_count() == 2

    def verify_fn(pw: str) -> bool:
        return pw == "CorrectPass!"

    prompt = AppLockPrompt(
        pid=12345,
        exe_name="calc.exe",
        verify_password_fn=verify_fn,
        lockout=lockout,
        settings_store=settings,
        keystore=ks,
    )
    qtbot.addWidget(prompt)

    prompt.pw_input.setText("CorrectPass!")
    with patch("mylocker.app.applock.prompt.resume_process"):
        prompt._verify()

    assert prompt.resolved is True
    assert lockout.get_fail_count() == 0


def test_applock_prompt_failure_increments_lockout_counter(lockout_setup, qtbot):
    ks, lockout, settings, _ = lockout_setup
    assert lockout.get_fail_count() == 0

    def verify_fn(pw: str) -> bool:
        return False

    prompt = AppLockPrompt(
        pid=12345,
        exe_name="calc.exe",
        verify_password_fn=verify_fn,
        lockout=lockout,
        settings_store=settings,
        keystore=ks,
    )
    qtbot.addWidget(prompt)

    with patch.object(prompt, "_capture_intruder_photo"):
        prompt.pw_input.setText("WrongPass1")
        prompt._verify()

    assert lockout.get_fail_count() == 1
    assert len(lockout.get_intruder_log()) == 1
    assert "attempts remaining" in prompt.error_lbl.text()


def test_applock_prompt_active_lockout_blocks_entry(lockout_setup, qtbot):
    ks, lockout, settings, _ = lockout_setup
    # Record 3 failures to trigger 30s lockout
    lockout.record_failure()
    lockout.record_failure()
    lockout.record_failure()
    assert lockout.get_lockout_seconds() > 0

    def verify_fn(pw: str) -> bool:
        return True

    lock_cb = MagicMock()
    prompt = AppLockPrompt(
        pid=12345,
        exe_name="calc.exe",
        verify_password_fn=verify_fn,
        lockout=lockout,
        settings_store=settings,
        keystore=ks,
        on_lockout_triggered=lock_cb,
    )
    qtbot.addWidget(prompt)

    assert not prompt.pw_input.isEnabled()
    assert "Too many failed attempts" in prompt.error_lbl.text()
    lock_cb.assert_called_once()


def test_applock_prompt_lockout_triggers_lock_callback(lockout_setup, qtbot):
    ks, lockout, settings, _ = lockout_setup
    # Record 2 failures
    lockout.record_failure()
    lockout.record_failure()

    def verify_fn(pw: str) -> bool:
        return False

    lock_cb = MagicMock()
    prompt = AppLockPrompt(
        pid=12345,
        exe_name="calc.exe",
        verify_password_fn=verify_fn,
        lockout=lockout,
        settings_store=settings,
        keystore=ks,
        on_lockout_triggered=lock_cb,
    )
    qtbot.addWidget(prompt)

    # 3rd failure reaches lockout threshold
    with patch.object(prompt, "_capture_intruder_photo"):
        prompt.pw_input.setText("WrongPass3")
        prompt._verify()

    assert lockout.get_fail_count() == 3
    assert lockout.get_lockout_seconds() > 0
    assert not prompt.pw_input.isEnabled()
    assert "Too many failed attempts" in prompt.error_lbl.text()
    lock_cb.assert_called_once()


def test_applock_prompt_self_destruct_wipe_policy(lockout_setup, qtbot):
    ks, lockout, settings, _ = lockout_setup
    settings.set("self_destruct_enabled", True)

    mock_keystore = MagicMock()

    # Preload failure count to 19
    state = lockout._load_state()
    state["fails"] = 19
    lockout._save_state(state)
    assert lockout.get_fail_count() == 19

    def verify_fn(pw: str) -> bool:
        return False

    lock_cb = MagicMock()
    prompt = AppLockPrompt(
        pid=12345,
        exe_name="calc.exe",
        verify_password_fn=verify_fn,
        lockout=lockout,
        settings_store=settings,
        keystore=mock_keystore,
        on_lockout_triggered=lock_cb,
    )
    qtbot.addWidget(prompt)

    with patch.object(prompt, "_capture_intruder_photo"):
        prompt.pw_input.setText("Attempt20Wrong")
        prompt._verify()

    assert lockout.get_fail_count() == 20
    mock_keystore.self_destruct.assert_called_once()
    assert "self-destruct activated" in prompt.error_lbl.text().lower()
    lock_cb.assert_called_once()


def test_applock_prompt_captures_intruder_photo_on_failure(lockout_setup, qtbot):
    ks, lockout, settings, _ = lockout_setup

    def verify_fn(pw: str) -> bool:
        return False

    prompt = AppLockPrompt(
        pid=12345,
        exe_name="calc.exe",
        verify_password_fn=verify_fn,
        lockout=lockout,
        settings_store=settings,
        keystore=ks,
    )
    qtbot.addWidget(prompt)

    mock_thread = MagicMock()
    with patch("mylocker.app.system.webcam.IntruderPhotoCaptureThread", return_value=mock_thread) as mock_cls:
        prompt.pw_input.setText("WrongPassWebcam")
        prompt._verify()

        mock_cls.assert_called_once_with(lockout.vault_dir, lockout, parent=prompt)
        mock_thread.start.assert_called_once()
