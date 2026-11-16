"""
test_performance_and_responsiveness.py — Regression tests for fast Argon2id unlock,
speed optimization, lazy Dashboard tabs, non-blocking loading, and UI responsiveness.
"""

from __future__ import annotations

import os
import time
from unittest.mock import patch

import pytest
from PySide6.QtCore import Qt

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from mylocker.app.core.crypto import CryptoError, zero_bytes
from mylocker.app.core.kdf import ARGON2_MIN_MEMORY_KIB, KDFParams, benchmark_argon2id, derive_kek
from mylocker.app.core.keystore import Keystore
from mylocker.app.core.lockout import LockoutManager, make_lockout_signing_key
from mylocker.app.core.session import get_session
from mylocker.app.core.settings_store import SettingsStore
from mylocker.app.core.vault import Vault
from mylocker.app.ui.dashboard import Dashboard, FileCard
from mylocker.app.ui.login import LoginScreen


def _fast_kdf() -> KDFParams:
    return KDFParams.new_with_fresh_salt(memory_kib=1024, time_cost=1)


def _slow_kdf() -> KDFParams:
    return KDFParams.new_with_fresh_salt(memory_kib=256 * 1024, time_cost=23)


@pytest.fixture(autouse=True)
def _clean_session():
    get_session().lock()
    yield
    get_session().lock()


class TestFastKDFAndSpeedOptimization:
    def test_benchmark_argon2id_targets_responsive_owasp_params(self):
        params = benchmark_argon2id(target_seconds=0.25)
        assert params.memory_kib >= ARGON2_MIN_MEMORY_KIB
        assert params.time_cost >= 3
        assert params.parallelism == 4

        t0 = time.perf_counter()
        kek = derive_kek("VerifySpeedPass#2026", params)
        dt = time.perf_counter() - t0
        assert len(kek) == 32
        assert dt < 1.0  # Well below legacy ~1.0-1.5s per slot

    def test_keystore_optimize_unlock_speed_preserves_keys_and_rejects_bad_password(self, tmp_path):
        vault_dir = tmp_path / "OptVault"
        ks = Keystore(vault_dir)
        slow_params = KDFParams.new_with_fresh_salt(memory_kib=128 * 1024, time_cost=8)
        mk, rec_key = ks.setup("OptMasterPass#2026", benchmark=False, kdf_params=slow_params)
        mk_copy = bytes(mk)
        zero_bytes(mk)

        sk1 = make_lockout_signing_key(ks.config.kdf_params.salt)
        settings = SettingsStore(vault_dir, sk1)
        settings.set("idle_timeout_minutes", 19)
        lockout = LockoutManager(vault_dir, sk1)

        # Wrong password must fail cleanly without changing config
        with pytest.raises(CryptoError):
            ks.optimize_unlock_speed("WrongPassword#123")

        ks.config.load()
        assert ks.config.kdf_params.memory_kib == 128 * 1024

        # Valid password optimizes to fast OWASP params
        fast_target = KDFParams.new_with_fresh_salt(memory_kib=64 * 1024, time_cost=4)
        with patch("mylocker.app.core.keystore.benchmark_argon2id", return_value=fast_target):
            new_params = ks.optimize_unlock_speed("OptMasterPass#2026")

        assert new_params.memory_kib == 64 * 1024
        assert new_params.time_cost == 4

        # Master key and recovery key still unwrap identically
        mk_after = ks.unlock_with_password("OptMasterPass#2026")
        assert bytes(mk_after) == mk_copy
        zero_bytes(mk_after)

        mk_rec = ks.unlock_with_recovery_key(rec_key)
        assert bytes(mk_rec) == mk_copy
        zero_bytes(mk_rec)

        # Auxiliary stores survive salt update via rekey
        sk2 = make_lockout_signing_key(new_params.salt)
        settings.rekey(sk2)
        lockout.rekey(sk2, reset_failures=True)
        assert SettingsStore(vault_dir, sk2).get("idle_timeout_minutes") == 19
        LockoutManager(vault_dir, sk2).check_lockout()


class TestDashboardLazyTabsAndAsyncLoad:
    def test_dashboard_lazy_loads_security_tabs_without_startup_subprocess(self, qtbot, tmp_path):
        vault_dir = tmp_path / "LazyVault"
        ks = Keystore(vault_dir)
        mk, _ = ks.setup("LazyVaultPass#2026", benchmark=False, kdf_params=_fast_kdf())
        get_session().unlock(mk, is_decoy=False)

        vault = Vault(vault_dir)
        vault.initialise()
        ks.config.load()
        sk = make_lockout_signing_key(ks.config.kdf_params.salt)
        lockout = LockoutManager(vault_dir, sk)
        settings_store = SettingsStore(vault_dir, sk)

        with patch("mylocker.app.system.startup.is_run_at_startup_enabled", return_value=False) as mock_startup:
            dash = Dashboard(
                vault=vault,
                session=get_session(),
                keystore=ks,
                lockout=lockout,
                settings_store=settings_store,
            )
            qtbot.addWidget(dash)
            try:
                assert dash._intruder_ui is None
                assert dash._applock_ui is None
                assert dash._settings_ui is None
                assert mock_startup.call_count == 0

                # Navigating to App Lock constructs it lazily & connects signal
                seen_apps: list[set] = []
                dash.protected_apps_changed.connect(seen_apps.append)
                qtbot.mouseClick(dash._nav_items[6], Qt.MouseButton.LeftButton)
                assert dash._applock_ui is not None
                dash._applock_ui.protected_apps_changed.emit({"calc.exe"})
                assert seen_apps == [{"calc.exe"}]

                # Navigating to Settings constructs it lazily & connects signal
                seen_settings: list[bool] = []
                dash.settings_updated.connect(lambda: seen_settings.append(True))
                qtbot.mouseClick(dash._nav_items[7], Qt.MouseButton.LeftButton)
                assert dash._settings_ui is not None
                dash._settings_ui.settings_updated.emit()
                assert seen_settings == [True]
            finally:
                dash.cleanup()

    def test_dashboard_async_load_and_speed_banner(self, qtbot, tmp_path):
        vault_dir = tmp_path / "AsyncVault"
        ks = Keystore(vault_dir)
        mk, _ = ks.setup("AsyncPass#2026!", benchmark=False, kdf_params=_fast_kdf())
        ks.config.load()
        ks.config._data["kdf_params"]["memory_kib"] = 256 * 1024
        ks.config._data["kdf_params"]["time_cost"] = 23

        get_session().unlock(mk, is_decoy=False)
        vault = Vault(vault_dir)
        vault.initialise()
        sample = tmp_path / "doc.txt"
        sample.write_bytes(b"hello async dashboard")
        vault.add_file(sample)

        sk = make_lockout_signing_key(ks.config.kdf_params.salt)
        lockout = LockoutManager(vault_dir, sk)
        settings_store = SettingsStore(vault_dir, sk)

        dash = Dashboard(
            vault=vault,
            session=get_session(),
            keystore=ks,
            lockout=lockout,
            settings_store=settings_store,
        )
        qtbot.addWidget(dash)
        dash.show()
        try:
            with qtbot.waitSignal(dash.files_loaded, timeout=5000):
                dash.load_files_async(initial=True, unlock_elapsed_s=1.2)

            assert len(dash._current_entries) == 1
            assert dash._speed_banner.isVisible() is True

            # Dismiss hides the banner
            dash._dismiss_speed_banner()
            assert dash._speed_banner.isVisible() is False

            # FileCards avoid expensive software drop-shadow effects
            card = FileCard(dash._current_entries[0])
            qtbot.addWidget(card)
            assert card.graphicsEffect() is None
        finally:
            dash.cleanup()

    def test_speed_banner_auto_dismisses_at_90fps_and_pauses_on_hover(self, qtbot, tmp_path):
        vault_dir = tmp_path / "AutoDismissVault"
        ks = Keystore(vault_dir)
        mk, _ = ks.setup("AutoDismissPass#2026!", benchmark=False, kdf_params=_fast_kdf())
        get_session().unlock(mk, is_decoy=False)
        vault = Vault(vault_dir)
        vault.initialise()
        ks.config.load()
        sk = make_lockout_signing_key(ks.config.kdf_params.salt)
        dash = Dashboard(
            vault=vault,
            session=get_session(),
            keystore=ks,
            lockout=LockoutManager(vault_dir, sk),
            settings_store=SettingsStore(vault_dir, sk),
        )
        qtbot.addWidget(dash)
        dash.show()
        try:
            assert dash._speed_banner._timeout_ms == 5000
            assert dash._speed_banner._FRAME_INTERVAL_MS == 11  # ~90 FPS

            # Child Optimize Speed button must NOT carry a nested QGraphicsEffect inside parent opacity effect
            assert dash._optimize_btn.graphicsEffect() is None

            # Use short timeout for deterministic fast unit testing of 90fps collapse
            dash._speed_banner._timeout_ms = 140
            dash._speed_banner.show_animated()
            assert dash._speed_banner.isVisible() is True

            # Hovering the Optimize Speed button itself pauses countdown and prevents vanishing
            from PySide6.QtCore import QEvent
            from PySide6.QtWidgets import QApplication
            QApplication.sendEvent(dash._optimize_btn, QEvent(QEvent.Type.Enter))
            qtbot.wait(190)
            assert dash._speed_banner._hovered is True
            assert dash._speed_banner.isVisible() is True
            assert dash._speed_banner_dismissed is False

            # Leaving button resumes countdown and smoothly fades/collapses banner out
            QApplication.sendEvent(dash._optimize_btn, QEvent(QEvent.Type.Leave))
            qtbot.waitUntil(lambda: not dash._speed_banner.isVisible(), timeout=2000)
            assert dash._speed_banner_dismissed is True
        finally:
            dash.cleanup()


class TestLoginScreenResponsivenessFixes:
    def test_wrong_password_reenables_unlock_button(self, qtbot, tmp_path):
        vault_dir = tmp_path / "RetryVault"
        ks = Keystore(vault_dir)
        mk, _ = ks.setup("RightPass#2026!", benchmark=False, kdf_params=_fast_kdf())
        zero_bytes(mk)
        sk = make_lockout_signing_key(ks.config.kdf_params.salt)
        lockout = LockoutManager(vault_dir, sk)

        login = LoginScreen(ks, lockout)
        qtbot.addWidget(login)
        login.show()

        login._pw_input.setText("WrongAttempt#1")
        qtbot.mouseClick(login._unlock_btn, Qt.MouseButton.LeftButton)

        qtbot.waitUntil(lambda: bool(login._error_lbl.text()), timeout=5000)
        assert login._unlock_btn.isEnabled() is True
        assert login._unlock_btn.text().strip().startswith("Unlock")

    def test_missing_keyfile_fails_instantaneously_without_running_argon2(self, qtbot, tmp_path):
        vault_dir = tmp_path / "KfVault"
        kf = tmp_path / "usb.key"
        kf.write_bytes(os.urandom(32))

        ks = Keystore(vault_dir)
        mk, _ = ks.setup("KeyfilePass#2026!", keyfile_path=str(kf), benchmark=False, kdf_params=_fast_kdf())
        zero_bytes(mk)
        sk = make_lockout_signing_key(ks.config.kdf_params.salt)
        lockout = LockoutManager(vault_dir, sk)

        login = LoginScreen(ks, lockout)
        qtbot.addWidget(login)
        login.show()

        login._pw_input.setText("KeyfilePass#2026!")
        login._kf_edit.setText("")  # Missing keyfile

        with patch.object(ks, "unlock_with_password") as mock_unlock:
            qtbot.mouseClick(login._unlock_btn, Qt.MouseButton.LeftButton)
            assert mock_unlock.call_count == 0

        assert "key file" in login._error_lbl.text().lower()
        assert lockout.get_fail_count() == 0
