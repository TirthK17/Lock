"""
lock_controller.py — Central security coordinator for vault locking and cleanup.

Audit Requirements (Phase 4, Items 29, 30, 31, 34, 35):
  - Closes all active viewers (Gallery, Video Player, Dialogs) on lock.
  - Zeros all key material via Session.lock().
  - Wipes all temporary external folders securely.
  - Cleans only vault-opened recent files.
  - Resets UI state to the login screen.
  - Listens to idle timeout, panic hotkey, sleep/suspend, and session lock.
"""

from __future__ import annotations

import atexit
import logging
from pathlib import Path
from typing import Optional, Set

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from ..core.secure_delete import wipe_temp_directory
from ..core.session import get_session
from .hotkeys import PanicHotkeyManager
from .idle_monitor import IdleMonitor
from .privacy_cleanup import clean_vault_recent_files, clear_clipboard
from .screenshot import apply_screenshot_protection

logger = logging.getLogger(__name__)


class LockController(QObject):
    """Central manager for application locking, window tracking, and cleanup."""

    locked = Signal(str)  # Emits reason ("user", "idle", "panic", "session_lock", "suspend", "quit")

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._viewers: Set[QWidget] = set()
        self._top_level_windows: Set[QWidget] = set()
        self._temp_dirs: Set[Path] = set()
        self._opened_filenames: Set[str] = set()
        self._screenshot_protected: bool = True

        self._idle_monitor = IdleMonitor()
        self._idle_monitor.idle_timeout.connect(lambda: self.lock("idle"))

        self._hotkey_manager = PanicHotkeyManager()
        self._hotkey_manager.panic_triggered.connect(lambda: self.lock("panic"))

        # Register emergency exit hook
        atexit.register(self.emergency_cleanup)

    # ── Registrations ─────────────────────────────────────────────────────────

    def register_window(self, window: QWidget) -> None:
        """Register a top-level window for screenshot protection and lifetime tracking."""
        self._top_level_windows.add(window)
        if self._screenshot_protected:
            apply_screenshot_protection(window, True)
        window.destroyed.connect(lambda: self._top_level_windows.discard(window))

    def register_viewer(self, viewer: QWidget) -> None:
        """Register an active file viewer (gallery, video player, etc.)."""
        self._viewers.add(viewer)
        if self._screenshot_protected:
            apply_screenshot_protection(viewer, True)
        viewer.destroyed.connect(lambda: self._viewers.discard(viewer))

    def unregister_viewer(self, viewer: QWidget) -> None:
        self._viewers.discard(viewer)

    def register_temp_dir(self, temp_dir: Path) -> None:
        """Track a temporary directory created for external file viewing."""
        self._temp_dirs.add(temp_dir)

    def unregister_temp_dir(self, temp_dir: Path) -> None:
        self._temp_dirs.discard(temp_dir)

    def track_opened_filename(self, filename: str) -> None:
        """Track a filename for targeted Recent Files cleanup."""
        self._opened_filenames.add(filename)

    # ── Configuration ─────────────────────────────────────────────────────────

    def set_idle_timeout(self, minutes: float, enabled: bool = True) -> None:
        """Configure the inactivity auto-lock timeout."""
        self._idle_monitor.set_idle_minutes(minutes)
        if enabled:
            self._idle_monitor.start()
        else:
            self._idle_monitor.stop()

    def start_listeners(self) -> None:
        """Start global panic hotkey and idle monitor."""
        self._hotkey_manager.start()
        self._idle_monitor.start()

    def stop_listeners(self) -> None:
        """Stop background listeners."""
        self._hotkey_manager.stop()
        self._idle_monitor.stop()

    def set_screenshot_protection(self, enable: bool) -> None:
        """Enable or disable screenshot protection across all tracked windows."""
        self._screenshot_protected = enable
        for win in list(self._top_level_windows) + list(self._viewers):
            try:
                apply_screenshot_protection(win, enable)
            except Exception:
                pass

    # ── Central Lock ──────────────────────────────────────────────────────────

    def lock(self, reason: str = "user") -> None:
        """Instantly lock the vault and execute complete security cleanup."""
        logger.warning("LockController: Locking vault (reason=%s).", reason)
        self._idle_monitor.stop()

        # 1. Close all active viewers immediately
        for viewer in list(self._viewers):
            try:
                viewer.close()
            except Exception as exc:
                logger.debug("Error closing viewer: %s", exc)
        self._viewers.clear()

        # 2. Securely wipe all external temporary directories
        for temp_dir in list(self._temp_dirs):
            try:
                wipe_temp_directory(temp_dir)
            except Exception as exc:
                logger.debug("Error wiping temp dir %s: %s", temp_dir, exc)
        self._temp_dirs.clear()

        # 3. Clean up targeted Recent Files entries created for vault files
        if self._opened_filenames:
            try:
                clean_vault_recent_files(self._opened_filenames)
            except Exception as exc:
                logger.debug("Error cleaning recent files: %s", exc)
            self._opened_filenames.clear()

        # 4. Wipe clipboard
        clear_clipboard()

        # 5. Zero session Master Key in RAM
        get_session().lock()

        # 6. Notify UI to return to login screen
        self.locked.emit(reason)

    def emergency_cleanup(self) -> None:
        """Atexit / emergency handler to ensure no plaintext artifacts remain on disk."""
        for temp_dir in list(self._temp_dirs):
            try:
                wipe_temp_directory(temp_dir)
            except Exception:
                pass
        self._temp_dirs.clear()

        if self._opened_filenames:
            clean_vault_recent_files(self._opened_filenames)
            self._opened_filenames.clear()

        get_session().lock()


# ── Global Singleton ──────────────────────────────────────────────────────────
_instance: Optional[LockController] = None


def get_lock_controller() -> LockController:
    """Return the global LockController singleton."""
    global _instance
    if _instance is None:
        _instance = LockController()
    return _instance


__all__ = ["LockController", "get_lock_controller"]
