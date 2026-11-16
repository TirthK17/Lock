"""
watcher.py — Background process watcher for the App Lock feature.

Watches running processes via psutil. When a protected app is detected:
  1. Immediately suspend all its threads using NtSuspendProcess / pywin32.
  2. Show a fullscreen topmost password overlay.
  3. On correct password: resume the process.
  4. On cancel or timeout: terminate the process.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional, Set

import psutil

logger = logging.getLogger(__name__)


class ProcessWatcher(threading.Thread):
    """Background thread polling for protected processes."""

    def __init__(
        self,
        protected_exes: Set[str],
        on_app_launched: Callable[[int, str], None],
        poll_interval: float = 0.5,
    ) -> None:
        """
        Args:
            protected_exes: Set of executable names in lowercase (e.g. {'chrome.exe', 'telegram.exe'}).
            on_app_launched: Callback(pid: int, exe_name: str) called when protected app starts.
            poll_interval: How often to scan for new processes (seconds).
        """
        super().__init__(daemon=True, name="AppLockWatcher")
        self._protected = {e.lower() for e in protected_exes}
        self._callback = on_app_launched
        self._interval = poll_interval
        self._running = False
        self._known_pids: Set[int] = set()
        self.last_heartbeat: float = time.monotonic()

    def update_protected(self, exes: Set[str]) -> None:
        self._protected = {e.lower() for e in exes}

    def is_healthy(self, max_gap: float = 5.0) -> bool:
        """Check if watcher is running and actively heartbeating."""
        return self._running and (time.monotonic() - self.last_heartbeat <= max_gap)

    def run(self) -> None:
        self._running = True
        # Seed known PIDs
        try:
            self._known_pids = set(psutil.pids())
        except Exception:
            pass

        while self._running:
            self.last_heartbeat = time.monotonic()
            try:
                current_pids = set(psutil.pids())
                new_pids = current_pids - self._known_pids
                self._known_pids = current_pids

                for pid in new_pids:
                    try:
                        proc = psutil.Process(pid)
                        name = proc.name().lower()
                        if name in self._protected:
                            logger.info("Protected process detected: %s (PID %d)", name, pid)
                            self._callback(pid, name)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue

            except Exception as exc:
                logger.debug("Error in watcher loop: %s", exc)

            time.sleep(self._interval)

    def stop(self) -> None:
        self._running = False


class AppLockSupervisor(threading.Thread):
    """Supervisor thread monitoring the ProcessWatcher heartbeat and restarting if killed (Item 44)."""

    def __init__(
        self,
        protected_exes: Set[str],
        on_app_launched: Callable[[int, str], None],
        poll_interval: float = 0.5,
        supervisor_check_interval: float = 2.0,
    ) -> None:
        super().__init__(daemon=True, name="AppLockSupervisor")
        self.protected_exes = protected_exes
        self.on_app_launched = on_app_launched
        self.poll_interval = poll_interval
        self.supervisor_check_interval = supervisor_check_interval
        self._running = False
        self.watcher: Optional[ProcessWatcher] = None

    def start_watching(self) -> None:
        self._running = True
        self._start_child_watcher()
        self.start()

    def _start_child_watcher(self) -> None:
        self.watcher = ProcessWatcher(
            protected_exes=self.protected_exes,
            on_app_launched=self.on_app_launched,
            poll_interval=self.poll_interval,
        )
        self.watcher.start()
        logger.info("Child ProcessWatcher started.")

    def update_protected(self, exes: Set[str]) -> None:
        self.protected_exes = exes
        if self.watcher:
            self.watcher.update_protected(exes)

    def run(self) -> None:
        while self._running:
            time.sleep(self.supervisor_check_interval)
            if not self._running:
                break

            if self.watcher is None or not self.watcher.is_alive():
                logger.warning("ProcessWatcher died unexpectedly! Supervisor is restarting it.")
                self._start_child_watcher()

    def stop(self) -> None:
        self._running = False
        if self.watcher:
            self.watcher.stop()


__all__ = ["ProcessWatcher", "AppLockSupervisor"]

