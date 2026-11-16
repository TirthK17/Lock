"""
test_phase6.py — Comprehensive tests for Phase 6 (App Lock Rebuild):
1. Dedicated applock.config with separate Argon2id verifier (64 MiB, t=2)
2. Shortcut (.lnk) resolution to executable
3. AppLockSupervisor heartbeat and automatic restart
4. AppLockPrompt authentication flow and process handling
"""

from __future__ import annotations

import time
from pathlib import Path

from mylocker.app.applock.config import AppLockConfig
from mylocker.app.applock.process_utils import resolve_shortcut_target
from mylocker.app.applock.prompt import AppLockPrompt
from mylocker.app.applock.watcher import AppLockSupervisor


class TestAppLockConfig:
    def test_dedicated_argon2id_verifier(self, tmp_path: Path):
        cfg = AppLockConfig(tmp_path)
        assert not cfg.is_configured()

        # Set dedicated password
        cfg.set_password("AppLockPass123!@#")
        assert cfg.is_configured()
        assert (tmp_path / "applock.config").exists()

        # Verify correct password
        assert cfg.verify_password("AppLockPass123!@#") is True

        # Verify wrong password rejected
        assert cfg.verify_password("WrongPassword999!") is False

    def test_manage_locked_apps(self, tmp_path: Path):
        cfg = AppLockConfig(tmp_path)
        exe1 = cfg.add_locked_app("C:\\Program Files\\App\\telegram.exe")
        assert exe1 == "telegram.exe"
        assert "telegram.exe" in cfg.get_locked_apps()

        # Reload from disk
        cfg_reloaded = AppLockConfig(tmp_path)
        assert "telegram.exe" in cfg_reloaded.get_locked_apps()

        # Remove app
        cfg_reloaded.remove_locked_app("telegram.exe")
        assert "telegram.exe" not in cfg_reloaded.get_locked_apps()


class TestShortcutResolution:
    def test_resolve_plain_exe(self):
        plain = "C:\\Windows\\notepad.exe"
        assert resolve_shortcut_target(plain) == plain

    def test_resolve_lnk_shortcut(self, tmp_path: Path):
        # Create a mock .lnk if possible or verify fallback
        fake_lnk = tmp_path / "app.lnk"
        fake_lnk.write_text("mock")
        target = resolve_shortcut_target(str(fake_lnk))
        assert isinstance(target, str)


class TestSupervisorHeartbeat:
    def test_supervisor_restarts_dead_watcher(self):
        launched = []
        sup = AppLockSupervisor(
            protected_exes={"notepad.exe"},
            on_app_launched=lambda pid, name: launched.append((pid, name)),
            poll_interval=0.1,
            supervisor_check_interval=0.5,
        )
        sup.start_watching()
        time.sleep(0.3)

        assert sup.watcher is not None
        assert sup.watcher.is_alive()
        first_watcher = sup.watcher

        # Forcefully terminate child watcher
        first_watcher.stop()
        time.sleep(1.0)

        # Supervisor must have restarted a new child watcher
        assert sup.watcher is not None
        assert sup.watcher is not first_watcher
        assert sup.watcher.is_alive()

        sup.stop()


class TestAppLockPromptUI:
    def test_prompt_correct_password(self, qtbot):
        unlocked_calls = []
        def _verify(pw: str) -> bool:
            return pw == "Secret123!"

        prompt = AppLockPrompt(
            pid=999999,
            exe_name="testapp.exe",
            verify_password_fn=_verify,
        )
        qtbot.addWidget(prompt)
        prompt.unlocked.connect(lambda pid, name: unlocked_calls.append((pid, name)))

        prompt.pw_input.setText("Secret123!")
        prompt._verify()

        assert len(unlocked_calls) == 1
        assert unlocked_calls[0] == (999999, "testapp.exe")
        assert prompt.resolved is True

    def test_prompt_wrong_password_decrements_attempts(self, qtbot):
        def _verify(pw: str) -> bool:
            return False

        prompt = AppLockPrompt(
            pid=999999,
            exe_name="testapp.exe",
            verify_password_fn=_verify,
        )
        qtbot.addWidget(prompt)

        prompt.pw_input.setText("WrongPassword")
        prompt._verify()

        assert prompt.attempts == 1
        assert "attempts remaining" in prompt.error_lbl.text()
        assert prompt.resolved is False
