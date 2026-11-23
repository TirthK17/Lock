"""
main.py — Main application entry point for Personal Vault.

Coordinates:
  - Single-instance enforcement via Win32 named Mutex
  - High-DPI scaling & Qt Application
  - Theme styling
  - Setup Wizard vs. Login Screen vs. Calculator Camouflage
  - Dashboard lifecycle & in-memory session management
  - Panic hotkey, idle monitor auto-lock, and system tray

Security note: SetupWizard, Dashboard, and CalculatorDisguise are constructed
lazily (only when needed) to minimize attack surface and memory footprint.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

if __package__ in (None, ""):
    _repo_root = str(Path(__file__).resolve().parent.parent)
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QStackedWidget

from mylocker.app.applock.config import AppLockConfig
from mylocker.app.applock.prompt import AppLockPrompt
from mylocker.app.applock.watcher import AppLockSupervisor
from mylocker.app.core.crypto import zero_bytes
from mylocker.app.core.keystore import Keystore
from mylocker.app.core.lockout import LockoutManager, make_lockout_signing_key
from mylocker.app.core.session import get_session
from mylocker.app.core.settings_store import SettingsStore
from mylocker.app.core.vault import Vault
from mylocker.app.system.acl import restrict_vault_acl
from mylocker.app.system.hider import exclude_from_search_indexing, hide_vault_directory
from mylocker.app.system.lock_controller import get_lock_controller
from mylocker.app.system.privacy_cleanup import ClipboardWiper
from mylocker.app.system.tray import VaultTrayIcon
from mylocker.app.ui.login import LoginScreen
from mylocker.app.ui.theme import apply_dark_theme

# Setup logging — NEVER log secrets, keys, file names or full paths
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("PersonalVault")

# Windows Single-Instance Mutex
MUTEX_NAME = "Global\\PersonalVaultSingleInstanceMutex_7B9A2F"
ERROR_ALREADY_EXISTS = 183

# Per-user settings file (stores vault location between runs)
_SETTINGS_PATH = Path(os.environ.get("APPDATA", Path.home())) / "PersonalVaultSettings.json"


def _load_vault_dir() -> Path:
    """Load persisted vault directory from settings file, or return the default."""
    try:
        if _SETTINGS_PATH.exists():
            data = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
            if "vault_dir" in data:
                return Path(data["vault_dir"])
    except Exception:
        pass
    local_app_data = os.environ.get("LOCALAPPDATA", str(Path.home()))
    return Path(local_app_data) / "PersonalVault"


def _save_vault_dir(vault_dir: Path) -> None:
    """Persist the chosen vault directory for next launch."""
    try:
        _SETTINGS_PATH.write_text(
            json.dumps({"vault_dir": str(vault_dir)}, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Could not save vault settings: %s", type(exc).__name__)


class SingleInstance:
    """Enforces that only one instance of Personal Vault runs at any time."""

    def __init__(self) -> None:
        self.mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
        self.already_running = (ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS)

    def close(self) -> None:
        if self.mutex_handle:
            ctypes.windll.kernel32.CloseHandle(self.mutex_handle)
            self.mutex_handle = None


class _WindowSecurityFilter(QObject):
    """Handles lock-on-minimize and tray-only behavior for the main stacked window."""

    def __init__(self, vault_app: "PersonalVaultApp") -> None:
        super().__init__()
        self._app = vault_app

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._app.stack:
            if event.type() == QEvent.Type.WindowStateChange:
                if (
                    self._app.stack.windowState() & Qt.WindowState.WindowMinimized
                    and get_session().is_unlocked
                    and self._app.settings.get("lock_on_minimize", True)
                ):
                    QTimer.singleShot(0, lambda: self._app.lock_vault("minimize"))
            elif event.type() == QEvent.Type.Close:
                if self._app.settings.get("tray_only_mode", False):
                    event.ignore()
                    if get_session().is_unlocked:
                        self._app.lock_vault("tray_hide")
                    self._app.stack.hide()
                    return True
        return False


class PersonalVaultApp:
    """Main application orchestrator.

    Widget construction is lazy:
      - SetupWizard  → built only when no vault config exists
      - Dashboard    → built only after successful authentication
      - CalculatorDisguise → built only when stealth mode is active
    """

    # Stack indices for named screens
    _IDX_LOGIN = 0
    _IDX_SETUP = 1   # appended dynamically
    _IDX_CALC  = 2   # appended dynamically
    _IDX_DASH  = 3   # appended dynamically

    def __init__(self, app: QApplication) -> None:
        self.app = app
        apply_dark_theme(self.app)

        # Load vault path (persisted between runs)
        self.vault_dir = _load_vault_dir()
        self.vault = Vault(self.vault_dir)
        self.keystore = Keystore(self.vault_dir)

        # Lockout Manager & Settings Store
        signing_key = b"PersonalVault-DefaultSaltKey-v1"
        has_config = self.keystore.config.exists()
        if has_config:
            try:
                self.keystore.config.load()
                signing_key = make_lockout_signing_key(self.keystore.config.kdf_params.salt)
            except Exception:
                pass
        self.lockout = LockoutManager(self.vault_dir, signing_key)
        self.settings = SettingsStore(self.vault_dir, signing_key, auto_create=has_config)

        # Central Lock Controller coordination (single owner of idle & panic hotkey listeners)
        self.lock_controller = get_lock_controller()
        self.lock_controller.locked.connect(self._on_central_locked)
        self._lock_connected = True

        idle_mins = float(self.settings.get("idle_timeout_minutes", 5.0))
        self.lock_controller.set_idle_timeout(idle_mins, enabled=False)

        self.clipboard_wiper = ClipboardWiper()
        self.applock_supervisor: Optional[AppLockSupervisor] = None
        self._applock_grace_until: dict[str, float] = {}

        # System Tray
        self.tray = VaultTrayIcon()
        self.tray.open_requested.connect(self.show_main_window)
        self.tray.lock_requested.connect(lambda: self.lock_vault("tray"))
        self.tray.panic_requested.connect(self.panic_lock)
        self.tray.exit_requested.connect(self.exit_app)
        self.tray.show()

        # Window container — always starts with the Login screen
        self.stack = QStackedWidget()
        self.stack.setWindowTitle("Personal Vault")
        self.stack.resize(1180, 740)
        self._win_filter = _WindowSecurityFilter(self)
        self.stack.installEventFilter(self._win_filter)
        self.stack.destroyed.connect(self._on_stack_destroyed)

        # Login screen is always index 0 (always pre-built)
        self.login_screen = LoginScreen(self.keystore, self.lockout, settings_store=self.settings)
        self.login_screen.login_success.connect(self._on_login_success)
        self.login_screen.recovery_requested.connect(self._on_recovery_requested)
        self.login_screen.switch_vault_requested.connect(self._on_switch_vault_requested)
        self.stack.addWidget(self.login_screen)   # index 0

        # Lazy references (built on demand)
        self._setup_wizard = None
        self._dashboard = None   # Dashboard, imported lazily
        self._calc_disguise = None   # CalculatorDisguise, imported lazily

        # Decide initial screen
        if not self.keystore.config.exists():
            self._show_setup_wizard()
        elif self.settings.get("calculator_disguise_enabled", False):
            self._show_calc_disguise()
        else:
            self.stack.setCurrentIndex(0)  # Login

        # Apply tray-only taskbar hiding if configured
        self._apply_tray_mode()

        # Show window IMMEDIATELY before non-critical background checks
        self.stack.show()

        # Defer heavy/background initialization (hotkey, AppLock supervisor, directory hardening)
        QTimer.singleShot(250, self._post_startup_init)

    def _post_startup_init(self) -> None:
        """Start panic hotkey, App Lock supervisor, and background hardening without delaying window show."""
        self.lock_controller._hotkey_manager.start()
        self._sync_applock_supervisor()
        if self.vault_dir.exists():
            import threading
            def _harden_bg() -> None:
                try:
                    hide_vault_directory(self.vault_dir)
                    exclude_from_search_indexing(self.vault_dir)
                except Exception as exc:
                    logger.debug("Windows hardening re-apply skipped: %s", type(exc).__name__)
            threading.Thread(target=_harden_bg, daemon=True, name="StartupHardening").start()

    def _apply_tray_mode(self) -> None:
        tray_only = bool(self.settings.get("tray_only_mode", False))
        was_visible = self.stack.isVisible()
        self.stack.setWindowFlag(Qt.WindowType.Tool, tray_only)
        if was_visible:
            self.stack.show()

    def _sync_applock_supervisor(self) -> None:
        try:
            cfg = AppLockConfig(self.vault_dir)
            exes = cfg.get_locked_apps()
            if exes:
                self._on_protected_apps_changed(exes)
        except Exception:
            pass

    # ── Lazy widget builders ──────────────────────────────────────────────────

    def _show_setup_wizard(self) -> None:
        """Build and show the first-run setup wizard (lazy)."""
        if self._setup_wizard is None:
            from mylocker.app.ui.setup_wizard import SetupWizard
            self._setup_wizard = SetupWizard(self.keystore, self.vault_dir)
            self._setup_wizard.setup_complete.connect(self._on_setup_completed)
            self._setup_wizard.vault_location_chosen.connect(
                lambda d: self._on_vault_location_chosen(d, persist=False)
            )
            self.stack.addWidget(self._setup_wizard)
        self.stack.setCurrentWidget(self._setup_wizard)

    def _ensure_calc_disguise(self) -> None:
        if self._calc_disguise is None:
            from mylocker.app.ui.calculator_disguise import CalculatorDisguise
            pin = str(self.settings.get("calculator_disguise_pin", "1337"))
            self._calc_disguise = CalculatorDisguise(secret_code=pin)
            self._calc_disguise.secret_entered.connect(self._on_calc_secret_entered)
            self.stack.addWidget(self._calc_disguise)

    def _show_calc_disguise(self) -> None:
        self._ensure_calc_disguise()
        if self._calc_disguise:
            pin = str(self.settings.get("calculator_disguise_pin", "1337"))
            self._calc_disguise.secret_code = pin
            self.stack.setCurrentWidget(self._calc_disguise)

    def _on_calc_secret_entered(self) -> None:
        self.stack.setCurrentIndex(0)  # Reveal Login screen

    def _ensure_dashboard(self) -> None:
        """Build the Dashboard widget the first time it is needed."""
        if self._dashboard is None:
            from mylocker.app.ui.dashboard import Dashboard
            self._dashboard = Dashboard(
                vault=self.vault,
                session=get_session(),
                keystore=self.keystore,
                lockout=self.lockout,
                settings_store=self.settings,
            )
            self._dashboard.lock_requested.connect(lambda: self.lock_vault("user"))
            self._dashboard.panic_requested.connect(self.panic_lock)
            self._dashboard.credentials_changed.connect(self._rekey_stores)
            self._dashboard.protected_apps_changed.connect(self._on_protected_apps_changed)
            self._dashboard.settings_updated.connect(self._on_settings_updated)
            self.stack.addWidget(self._dashboard)

    def _on_settings_updated(self) -> None:
        self.settings.load()
        idle_mins = float(self.settings.get("idle_timeout_minutes", 5.0))
        self.lock_controller.set_idle_timeout(idle_mins, enabled=get_session().is_unlocked)

        ss_enabled = bool(self.settings.get("screenshot_protection", True))
        self.lock_controller.set_screenshot_protection(ss_enabled)
        if self._dashboard is not None and hasattr(self._dashboard, "set_screenshot_protection"):
            self._dashboard.set_screenshot_protection(ss_enabled)

        self._apply_tray_mode()
        if self._calc_disguise:
            self._calc_disguise.secret_code = str(self.settings.get("calculator_disguise_pin", "1337"))

    # ── Signal handlers ───────────────────────────────────────────────────────

    def _on_switch_vault_requested(self) -> None:
        """Allow user to pick a different vault location from login screen."""
        from PySide6.QtWidgets import QFileDialog
        chosen = QFileDialog.getExistingDirectory(
            self.stack,
            "Select Vault Directory",
            str(self.vault_dir.parent if self.vault_dir.parent.exists() else Path.home()),
        )
        if chosen:
            chosen_path = Path(chosen).resolve()
            self._on_vault_location_chosen(chosen_path)
            if not self.keystore.config.exists():
                self._show_setup_wizard()
            else:
                self.stack.setCurrentIndex(0)

    def _on_vault_location_chosen(self, vault_dir: Path, persist: bool = True) -> None:
        """Re-point core objects to the chosen vault directory (optionally persisting path)."""
        self.vault_dir = Path(vault_dir).resolve()
        if persist:
            _save_vault_dir(self.vault_dir)
        self.vault = Vault(self.vault_dir)
        self.keystore = Keystore(self.vault_dir)

        signing_key = b"PersonalVault-DefaultSaltKey-v1"
        has_config = self.keystore.config.exists()
        if has_config:
            try:
                self.keystore.config.load()
                signing_key = make_lockout_signing_key(self.keystore.config.kdf_params.salt)
            except Exception:
                pass
        self.lockout = LockoutManager(self.vault_dir, signing_key)
        self.settings = SettingsStore(self.vault_dir, signing_key, auto_create=has_config)

        if self._setup_wizard:
            self._setup_wizard.set_vault_dir(self.vault_dir)
        self.login_screen.set_keystore(self.keystore, self.lockout, self.settings)
        self._sync_applock_supervisor()

    def _rekey_stores(self) -> None:
        """Re-derive SettingsStore and LockoutManager HMAC keys after password reset/change or key rotation."""
        try:
            if self.keystore.config.exists():
                self.keystore.config.load()
                sk = make_lockout_signing_key(self.keystore.config.kdf_params.salt)
                self.settings.rekey(sk)
                self.lockout.rekey(sk, reset_failures=True)
                self.login_screen.set_keystore(self.keystore, self.lockout, self.settings)
        except Exception as exc:
            logger.warning("Failed to rekey auxiliary stores: %s", type(exc).__name__)

    def _on_setup_completed(self, master_key: bytearray) -> None:
        """Called when first-run wizard finishes."""
        logger.info("Setup completed — configuring vault directory security.")
        try:
            # 1. Transfer key safely to the session and unlock FIRST
            get_session().unlock(master_key, is_decoy=False)

            # 2. Initialise vault directory structure, encrypted index and journal
            self.vault.initialise()

            # 3. Persist vault location now that setup succeeded
            _save_vault_dir(self.vault_dir)

            # 4. Apply Windows security attributes and ACLs
            hide_vault_directory(self.vault_dir)
            exclude_from_search_indexing(self.vault_dir)
            restrict_vault_acl(self.vault_dir)

            # 5. Configure lockout & settings signing key derived from KDF salt
            self.keystore.config.load()
            sk = make_lockout_signing_key(self.keystore.config.kdf_params.salt)
            self.lockout.rekey(sk, reset_failures=True)
            self.settings.rekey(sk)
            self.login_screen.set_keystore(self.keystore, self.lockout, self.settings)

            # 6. Activate system monitors & tray state
            self.tray.set_locked(False)
            idle_mins = float(self.settings.get("idle_timeout_minutes", 5.0))
            self.lock_controller.set_idle_timeout(idle_mins, enabled=True)

            # 7. Initialize Dashboard & transition smoothly
            self._ensure_dashboard()
            self.stack.setCurrentWidget(self._dashboard)
            self._dashboard.load_files(initial=True)
        except Exception as exc:
            logger.critical("Failed to complete vault setup: %s", type(exc).__name__)
            get_session().lock()
            QMessageBox.critical(
                self.stack,
                "Setup Error",
                "An error occurred during vault initialization. Please check folder permissions and try again. [ERR-SETUP-01]",
            )
            self._show_setup_wizard()

    def _on_login_success(self, master_key: bytearray, is_decoy: bool) -> None:
        """Called when master password or decoy password is validated."""
        logger.info("Unlock succeeded (decoy=%s).", is_decoy)
        get_session().unlock(master_key, is_decoy=is_decoy)
        self.tray.set_locked(False)
        idle_mins = float(self.settings.get("idle_timeout_minutes", 5.0))
        self.lock_controller.set_idle_timeout(idle_mins, enabled=True)

        elapsed_s = float(getattr(self.login_screen, "last_unlock_seconds", 0.0) or 0.0)
        self._ensure_dashboard()
        self.stack.setCurrentWidget(self._dashboard)
        self._dashboard.load_files_async(initial=True, unlock_elapsed_s=elapsed_s)

    def _on_recovery_requested(self) -> None:
        from PySide6.QtWidgets import QDialog

        from mylocker.app.ui.login import ForgotPasswordDialog
        dialog = ForgotPasswordDialog(self.keystore, self.lockout, self.stack)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            mk, pw_reset = dialog.get_result()
            if pw_reset:
                self._rekey_stores()
            if mk is not None:
                self._on_login_success(mk, is_decoy=False)
                if pw_reset and self._dashboard is not None and hasattr(self._dashboard, "_status_bar"):
                    self._dashboard._status_bar.showMessage(
                        "Master password reset successfully via recovery key.",
                        6000,
                    )

    def _on_protected_apps_changed(self, exes: set) -> None:
        if not self.applock_supervisor:
            self.applock_supervisor = AppLockSupervisor(
                protected_exes=exes,
                on_app_launched=self._on_locked_app_detected,
            )
            self.applock_supervisor.start_watching()
        else:
            self.applock_supervisor.update_protected(exes)

    def _on_locked_app_detected(self, pid: int, exe_name: str) -> None:
        """Triggered from watcher thread when a protected process starts."""
        import time as _t
        grace_until = self._applock_grace_until.get(exe_name.lower(), 0.0)
        if _t.monotonic() < grace_until:
            return
        from mylocker.app.applock.process_utils import suspend_process
        suspend_process(pid)
        # Post dialog onto Qt main thread
        QTimer.singleShot(0, lambda: self._show_applock_prompt(pid, exe_name))

    def _show_applock_prompt(self, pid: int, exe_name: str) -> None:
        import time as _t

        from mylocker.app.applock.config import AppLockConfig
        applock_cfg = AppLockConfig(self.vault_dir)

        def verify_fn(pw: str) -> bool:
            if applock_cfg.exists() and applock_cfg.verify_password(pw):
                return True
            try:
                mk = self.keystore.unlock_with_password(pw)
                zero_bytes(mk)
                return True
            except Exception:
                return False

        prompt = AppLockPrompt(
            pid=pid,
            exe_name=exe_name,
            verify_password_fn=verify_fn,
            parent=self.stack,
            lockout=self.lockout,
            settings_store=self.settings,
            keystore=self.keystore,
            on_lockout_triggered=lambda: self.lock_vault("lockout"),
        )
        if prompt.exec():
            mins = applock_cfg.get_grace_minutes()
            if mins > 0:
                self._applock_grace_until[exe_name.lower()] = _t.monotonic() + mins * 60.0

    # ── Vault lifecycle ───────────────────────────────────────────────────────

    def lock_vault(self, reason: str = "user") -> None:
        """Lock the vault centrally and idempotently via LockController."""
        logger.info("Lock requested (reason=%s).", reason)
        self.lock_controller.lock(reason)

    @property
    def panic_hotkey(self):
        return self.lock_controller._hotkey_manager

    def _on_stack_destroyed(self) -> None:
        if not getattr(self, "_lock_connected", False):
            return
        self._lock_connected = False
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                self.lock_controller.locked.disconnect(self._on_central_locked)
        except Exception:
            pass

    def _on_central_locked(self, reason: str) -> None:
        """Central callback fired by LockController when lock completes."""
        try:
            import shiboken6
            if not shiboken6.isValid(self.stack) or not shiboken6.isValid(self.login_screen):
                self._on_stack_destroyed()
                return
        except Exception:
            pass
        logger.info("Central lock completed (reason=%s). Transitioning screen.", reason)
        self.tray.set_locked(True)

        # Shut down VaultService worker thread cleanly and release Dashboard UI memory
        if self._dashboard is not None:
            self._dashboard.cleanup()
            self.stack.removeWidget(self._dashboard)
            self._dashboard.deleteLater()
            self._dashboard = None

        self.login_screen.set_keystore(self.keystore, self.lockout, self.settings)

        if self.settings.get("calculator_disguise_enabled", False):
            self._show_calc_disguise()
        else:
            self.stack.setCurrentIndex(0)

        if reason == "panic":
            self.stack.hide()
        elif reason != "window_close":
            self.show_main_window()

    def panic_lock(self) -> None:
        """Instantly lock, close everything, hide window and tray icon."""
        logger.warning("PANIC LOCK ACTIVATED.")
        self.lock_vault("panic")

    def show_main_window(self) -> None:
        self.stack.show()
        self.stack.activateWindow()
        self.stack.raise_()

    def exit_app(self) -> None:
        self.lock_vault("quit")
        if self.applock_supervisor:
            self.applock_supervisor.stop()
        self.lock_controller.stop_listeners()
        self.tray.hide()
        self.app.quit()


def main() -> None:
    from mylocker.app.system.single_instance import try_acquire_single_instance

    # High-DPI support
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("PersonalVault")
    app.setOrganizationName("PersonalVault")

    vault_app_ref: list[Optional[PersonalVaultApp]] = [None]

    def _activate():
        def _do_activate():
            if vault_app_ref[0]:
                vault_app_ref[0].show_main_window()
        QTimer.singleShot(0, _do_activate)

    # Named Pipe single-instance enforcement (Audit Item 42)
    si_mgr = try_acquire_single_instance(on_activate=_activate)
    if si_mgr is None:
        # Existing instance was activated
        logger.info("Another instance is already running and was brought to focus.")
        sys.exit(0)

    try:
        vault_app = PersonalVaultApp(app)
        vault_app_ref[0] = vault_app
        sys.exit(app.exec())
    finally:
        si_mgr.stop()


if __name__ == "__main__":
    main()
