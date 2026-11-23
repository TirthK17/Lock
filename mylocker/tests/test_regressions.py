"""
test_regressions.py — Phase 1 verification tests for Windows bug fixes.

Tests:
1. lockout.py: corrupt lockout.dat -> check_lockout raises LockoutError(300) once,
   file is rewritten, and after 300 s (mock time) unlock is allowed again.
2. lockout.py: wall clock moved +1 day -> lockout restarts from now, not 24 h.
3. idle_monitor: tick count above 2**31 still gives a correct idle time (mock GetTickCount).
4. vault.restore_file: two files named a.txt restore to a.txt and a (1).txt, nothing overwritten.
5. login._is_auth_failure: CryptoError("Authentication failed.") True; OSError False.
6. LockController.lock(): pre-lock hooks run before Session.lock().
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mylocker.app.core.crypto import CryptoError, generate_salt
from mylocker.app.core.lockout import LockoutError, LockoutManager, make_lockout_signing_key
from mylocker.app.core.session import get_session
from mylocker.app.core.vault import Vault
from mylocker.app.system.idle_monitor import get_idle_time_seconds
from mylocker.app.system.lock_controller import LockController

try:
    from mylocker.app.ui.login import _is_auth_failure
except ImportError:
    _is_auth_failure = None


# ── 1. Corrupt lockout.dat test ───────────────────────────────────────────────

def test_corrupt_lockout_dat_raises_and_resets(tmp_path: Path):
    """Corrupt lockout.dat raises LockoutError(300) once, file is rewritten,

    and after 300s unlock is allowed again.
    """
    sk = make_lockout_signing_key(generate_salt(16))
    lockout_file = tmp_path / "lockout.dat"
    lockout_file.write_bytes(b"CORRUPTED_NON_JSON_DATA_!!!\x00\xff")

    lm = LockoutManager(tmp_path, sk)

    # First check must raise LockoutError(300)
    with pytest.raises(LockoutError) as exc_info:
        lm.check_lockout()
    assert exc_info.value.remaining_seconds == pytest.approx(300.0, abs=5.0)

    # Corrupt file must have been rewritten to a valid structure
    assert lockout_file.exists()
    assert not lockout_file.read_bytes().startswith(b"CORRUPTED")

    # Before 300s, still locked out
    with pytest.raises(LockoutError):
        lm.check_lockout()

    # After 300s (mocked time), unlock is allowed again
    future_time = time.time() + 305.0
    future_mono = time.monotonic() + 305.0
    with patch("time.time", return_value=future_time), patch("time.monotonic", return_value=future_mono):
        lm.check_lockout()  # Should not raise


# ── 2. Wall clock moved +1 day test ──────────────────────────────────────────

def test_lockout_wall_clock_moved_forward(tmp_path: Path):
    """Wall clock moved +1 day -> lockout restarts from now, not 24h."""
    sk = make_lockout_signing_key(generate_salt(16))
    lm = LockoutManager(tmp_path, sk)

    # Record 3 failures to trigger 30s lockout
    for _ in range(3):
        lm.record_failure()

    # Verify locked out
    with pytest.raises(LockoutError) as exc1:
        lm.check_lockout()
    assert exc1.value.remaining_seconds <= 30.0

    # Simulate wall clock pushed forward by 1 day (+86,400s) while monotonic only advanced by 5s
    now_wall = time.time() + 86400.0
    now_mono = time.monotonic() + 5.0
    with patch("time.time", return_value=now_wall), patch("time.monotonic", return_value=now_mono):
        # Must detect clock warp and clamp/restart lockout from now (remaining ~25s), not 24h
        with pytest.raises(LockoutError) as exc2:
            lm.check_lockout()
        assert exc2.value.remaining_seconds <= 30.0
        assert exc2.value.remaining_seconds > 0.0


# ── 3. Idle monitor tick count wraparound above 2**31 ────────────────────────

def test_idle_monitor_tick_count_above_2_pow_31():
    """Tick count above 2**31 still gives a correct idle time (mock GetTickCount)."""
    # 2**31 + 5000 ms, user last input was 2000 ms before that
    current_tick = (1 << 31) + 5000  # 2147488648
    last_input_tick = (1 << 31) + 3000  # 2147486648 (2 seconds ago)

    mock_last_input = MagicMock()
    mock_last_input.dwTime = last_input_tick

    with patch("ctypes.windll.kernel32.GetTickCount", return_value=current_tick), \
         patch("ctypes.windll.user32.GetLastInputInfo", side_effect=lambda ptr: setattr(ptr._obj, "dwTime", last_input_tick) or True):
        idle = get_idle_time_seconds()
        assert idle == pytest.approx(2.0, abs=0.2)


# ── 4. Restore file collision avoidance test ──────────────────────────────────

def test_restore_file_avoids_overwriting_duplicate_names(tmp_path: Path):
    """Two files named a.txt restore to a.txt and a (1).txt, nothing overwritten."""
    session = get_session()
    fake_key = bytearray(b"\x07" * 32)
    session.unlock(fake_key, is_decoy=False)

    vault_dir = tmp_path / "Vault"
    vault_dir.mkdir()
    vault = Vault(vault_dir)
    vault.initialise()

    try:
        # Create two distinct source files with the same filename in different directories
        dir1 = tmp_path / "src1"
        dir1.mkdir()
        src1 = dir1 / "a.txt"
        src1.write_text("CONTENT_VERSION_ONE", encoding="utf-8")

        dir2 = tmp_path / "src2"
        dir2.mkdir()
        src2 = dir2 / "a.txt"
        src2.write_text("CONTENT_VERSION_TWO", encoding="utf-8")

        entry1 = vault.add_file(src1, vault_folder="/folder1", generate_thumbnail=False)
        entry2 = vault.add_file(src2, vault_folder="/folder2", generate_thumbnail=False)

        restore_dir = tmp_path / "restored"
        restore_dir.mkdir()

        # Restore first file
        dest1 = vault.restore_file(entry1.file_id, restore_dir)
        assert dest1.name == "a.txt"
        assert dest1.read_text(encoding="utf-8") == "CONTENT_VERSION_ONE"

        # Restore second file with same original_name to the same destination directory
        dest2 = vault.restore_file(entry2.file_id, restore_dir)
        assert dest2.name == "a (1).txt"
        assert dest2.read_text(encoding="utf-8") == "CONTENT_VERSION_TWO"

        # Ensure original a.txt was NOT overwritten
        assert dest1.read_text(encoding="utf-8") == "CONTENT_VERSION_ONE"
    finally:
        session.lock()


# ── 5. login._is_auth_failure classification test ─────────────────────────────

def test_login_is_auth_failure():
    """CryptoError('Authentication failed.') -> True; OSError -> False."""
    assert _is_auth_failure(CryptoError("Authentication failed.")) is True
    assert _is_auth_failure(CryptoError("Wrong password")) is True
    assert _is_auth_failure(CryptoError("Decryption failed")) is True

    # Environmental/I/O errors must NOT be counted as auth failure
    assert _is_auth_failure(OSError("Disk read error")) is False
    assert _is_auth_failure(FileNotFoundError("Keyfile missing")) is False
    assert _is_auth_failure(PermissionError("Access denied")) is False
    assert _is_auth_failure(ValueError("Corrupt JSON")) is False


# ── 6. LockController pre-lock hooks test ─────────────────────────────────────

def test_lock_controller_pre_lock_hooks_run_before_session_lock():
    """Pre-lock hooks run before Session.lock() zeroes the master key."""
    controller = LockController()
    session = get_session()
    fake_key = bytearray(b"\x42" * 32)
    session.unlock(fake_key, is_decoy=False)

    hook_called = False
    key_was_valid_in_hook = False

    def pre_lock_hook():
        nonlocal hook_called, key_was_valid_in_hook
        hook_called = True
        try:
            k = session.get_master_key()
            if len(k) == 32 and any(b != 0 for b in k):
                key_was_valid_in_hook = True
        except Exception:
            key_was_valid_in_hook = False

    controller.register_pre_lock_hook(pre_lock_hook)
    try:
        controller.lock(reason="test")
        assert hook_called is True
        assert key_was_valid_in_hook is True
        assert not session.is_unlocked
    finally:
        session.lock()
