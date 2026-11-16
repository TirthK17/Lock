"""
single_instance.py — Single-instance application enforcement via Windows Named Pipe.

Audit Requirement (Phase 5, Item 42):
  - Local named pipe server per user: \\\\.\\pipe\\PersonalVault_SingleInstance_<username>
  - If another instance is running, sends ACTIVATE message to bring it to front and exits.
  - If first instance, creates pipe server and listens for activation requests.
"""

from __future__ import annotations

import getpass
import logging
import os
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)


def _get_pipe_name() -> str:
    user = getpass.getuser() or os.environ.get("USERNAME", "default")
    # Clean username for pipe naming
    clean_user = "".join(c for c in user if c.isalnum() or c in ("-", "_"))
    return f"\\\\.\\pipe\\PersonalVault_SingleInstance_{clean_user}"


class SingleInstanceManager:
    """Manages the server side of the single instance named pipe."""

    def __init__(self, pipe_name: str, on_activate: Optional[Callable[[], None]] = None) -> None:
        self.pipe_name = pipe_name
        self.on_activate = on_activate
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._server_loop, daemon=True, name="SingleInstanceServer")
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def _server_loop(self) -> None:
        try:
            import win32file
            import win32pipe
        except ImportError:
            logger.debug("pywin32 named-pipe server unavailable; skipping single-instance listener.")
            return

        while self._running:
            pipe_handle = None
            try:
                pipe_handle = win32pipe.CreateNamedPipe(
                    self.pipe_name,
                    win32pipe.PIPE_ACCESS_DUPLEX,
                    win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT,
                    1,
                    65536,
                    65536,
                    0,
                    None,
                )

                # Wait for client connection
                win32pipe.ConnectNamedPipe(pipe_handle, None)

                # Read message
                _hr, data = win32file.ReadFile(pipe_handle, 64)
                if data and b"ACTIVATE" in data:
                    logger.info("Secondary instance requested window activation.")
                    if self.on_activate:
                        self.on_activate()

                win32file.WriteFile(pipe_handle, b"OK\n")
                win32file.FlushFileBuffers(pipe_handle)
                win32pipe.DisconnectNamedPipe(pipe_handle)
            except Exception as exc:
                if not self._running:
                    break
                logger.debug("Named pipe server iteration error: %s", exc)
            finally:
                if pipe_handle:
                    try:
                        win32file.CloseHandle(pipe_handle)
                    except Exception:
                        pass


def try_acquire_single_instance(on_activate: Optional[Callable[[], None]] = None) -> Optional[SingleInstanceManager]:
    """Attempt to become the single running instance.

    Returns:
        SingleInstanceManager if this process is the primary instance.
        None if another instance is already running (after activating it).
    """
    pipe_name = _get_pipe_name()

    try:
        import win32file

        # Try connecting to existing instance
        h_pipe = win32file.CreateFile(
            pipe_name,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0,
            None,
            win32file.OPEN_EXISTING,
            0,
            None,
        )

        # Connected! Another instance is running. Send ACTIVATE command.
        logger.info("Existing instance detected. Sending ACTIVATE message.")
        win32file.WriteFile(h_pipe, b"ACTIVATE\n")
        win32file.CloseHandle(h_pipe)
        return None  # Signal that an instance is already running

    except Exception:
        # Connection failed — no existing instance running. Become the primary instance.
        pass

    # Start primary server
    manager = SingleInstanceManager(pipe_name, on_activate)
    manager.start()
    return manager


__all__ = ["SingleInstanceManager", "try_acquire_single_instance"]
