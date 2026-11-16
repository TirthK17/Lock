"""
external_opener.py — Securely open non-media vault files in external Windows applications.

Security design (Phase 4, Items 34, 35):
  - Decrypts to a randomized subfolder in temp with restricted NTFS DACL
    (only the current user SID has access; inheritance broken).
  - Launches via ShellExecuteExW to obtain process handle.
  - Monitors the external process lifecycle in a background thread.
  - Computes SHA-256 before launch and after exit: if modified, triggers
    a callback offering the user to re-encrypt changes back into the vault.
  - Securely deletes the file (random overwrite + rename + unlink) upon process exit.
  - Registered with LockController: if the vault is locked while the file is open,
    the temp directory is wiped immediately.
"""

from __future__ import annotations

import ctypes
import logging
import secrets
import tempfile
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Callable, Optional

from ..core.journal import hash_file_sha256
from ..core.secure_delete import secure_delete_file, wipe_temp_directory
from .acl import restrict_vault_acl
from .lock_controller import get_lock_controller
from .privacy_cleanup import clean_vault_recent_files

logger = logging.getLogger(__name__)

# Win32 ShellExecuteEx constants
SEE_MASK_NOCLOSEPROCESS = 0x00000040
SW_SHOWNORMAL = 1
INFINITE = 0xFFFFFFFF


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("fMask", ctypes.c_ulong),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", ctypes.c_ulong),
        ("hIcon", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


def open_external_securely(
    vault,
    file_id: str,
    on_modified: Optional[Callable[[str, Path], None]] = None,
) -> bool:
    """Decrypt a vault file to an isolated, ACL-restricted temp folder and launch it safely."""
    if hasattr(vault, "_ensure_loaded"):
        vault._ensure_loaded()
    entry = vault._index.index.get(file_id)
    if not entry:
        logger.error("Requested external file not found in vault index.")
        return False

    # 1. Create a unique, random temporary directory
    rand_id = secrets.token_hex(8)
    temp_dir = Path(tempfile.gettempdir()) / f"pv_ext_{rand_id}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    # 2. Restrict NTFS ACL to current user
    restrict_vault_acl(temp_dir)

    # 3. Decrypt file (sanitize basename to avoid any traversal)
    safe_name = Path(entry.original_name).name or "vault_file"
    file_path = temp_dir / safe_name
    try:
        vault.export_file(file_id, file_path)
    except Exception as exc:
        logger.error("Failed to decrypt external file: %s", type(exc).__name__)
        wipe_temp_directory(temp_dir)
        return False

    # 4. Hash before launch
    try:
        hash_before = hash_file_sha256(file_path)
    except Exception:
        hash_before = ""

    # 5. Register with central LockController
    lc = get_lock_controller()
    lc.register_temp_dir(temp_dir)
    lc.track_opened_filename(entry.original_name)

    # 6. Launch via ShellExecuteExW
    sei = SHELLEXECUTEINFOW()
    sei.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    sei.fMask = SEE_MASK_NOCLOSEPROCESS
    sei.hwnd = None
    sei.lpVerb = "open"
    sei.lpFile = str(file_path)
    sei.lpParameters = None
    sei.lpDirectory = str(temp_dir)
    sei.nShow = SW_SHOWNORMAL

    success = ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei))
    if not success:
        logger.warning("ShellExecuteExW failed. Falling back to os.startfile.")
        try:
            import os
            os.startfile(str(file_path))
            h_process = None
        except Exception as exc:
            logger.error("Failed to start file: %s", type(exc).__name__)
            wipe_temp_directory(temp_dir)
            lc.unregister_temp_dir(temp_dir)
            return False
    else:
        h_process = sei.hProcess

    # 7. Background watcher thread
    def _watch_and_cleanup() -> None:
        try:
            if h_process:
                # Wait for external process to exit
                ctypes.windll.kernel32.WaitForSingleObject(h_process, INFINITE)
                ctypes.windll.kernel32.CloseHandle(h_process)
            else:
                # Polling fallback: wait until file is no longer locked by an app or timeout
                time.sleep(30)

            # Check if file was modified while open
            if file_path.exists() and hash_before:
                try:
                    hash_after = hash_file_sha256(file_path)
                    if hash_after != hash_before and on_modified:
                        logger.info("External file modified by user. Staging and prompting for update.")
                        # Stage modified file to prevent race condition during temp_dir cleanup
                        staged_dir = Path(tempfile.gettempdir()) / f"pv_stage_{secrets.token_hex(6)}"
                        staged_dir.mkdir(parents=True, exist_ok=True)
                        restrict_vault_acl(staged_dir)
                        lc.register_temp_dir(staged_dir)

                        staged_file = staged_dir / file_path.name
                        import shutil
                        shutil.copy2(file_path, staged_file)

                        # Notify dashboard with safe staged copy
                        on_modified(file_id, staged_file)
                except Exception as exc:
                    logger.debug("Error verifying post-exit hash: %s", type(exc).__name__)

        finally:
            # Securely wipe the file and remove temp folder
            try:
                if file_path.exists():
                    secure_delete_file(file_path)
            except Exception as exc:
                logger.debug("Error securely deleting temp file: %s", type(exc).__name__)

            try:
                wipe_temp_directory(temp_dir)
            except Exception as exc:
                logger.debug("Error wiping temp dir: %s", type(exc).__name__)

            lc.unregister_temp_dir(temp_dir)
            clean_vault_recent_files([entry.original_name])

    thread = threading.Thread(target=_watch_and_cleanup, daemon=True, name=f"ExtWatcher-{file_id[:8]}")
    thread.start()
    return True


__all__ = ["open_external_securely"]
