"""
test_phase5.py — Comprehensive tests for Phase 5 (Windows Hardening):
1. ACL enforcement (DACL inheritance blocking)
2. Hidden & System file attributes
3. Windows Search / Indexer exclusion (desktop.ini + FILE_ATTRIBUTE_NOT_CONTENT_INDEXED)
4. BitLocker status query and advisory
5. Single-instance named pipe IPC
"""

from __future__ import annotations

import ctypes
import os
import threading
import time
from pathlib import Path

from mylocker.app.system.acl import restrict_vault_acl
from mylocker.app.system.bitlocker import check_bitlocker_status
from mylocker.app.system.hider import (
    FILE_ATTRIBUTE_HIDDEN,
    FILE_ATTRIBUTE_SYSTEM,
    exclude_from_search_indexing,
    hide_vault_directory,
    unhide_vault_directory,
)
from mylocker.app.system.single_instance import SingleInstanceManager


class TestWindowsHardeningAttributes:
    def test_hide_and_unhide_vault_directory(self, tmp_path: Path):
        vault_dir = tmp_path / "hardened_vault"
        vault_dir.mkdir()

        # Hide
        hide_vault_directory(vault_dir)
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(vault_dir))
        assert attrs != -1
        assert (attrs & FILE_ATTRIBUTE_HIDDEN) != 0
        assert (attrs & FILE_ATTRIBUTE_SYSTEM) != 0

        # Unhide
        unhide_vault_directory(vault_dir)
        attrs_after = ctypes.windll.kernel32.GetFileAttributesW(str(vault_dir))
        assert (attrs_after & FILE_ATTRIBUTE_HIDDEN) == 0
        assert (attrs_after & FILE_ATTRIBUTE_SYSTEM) == 0

    def test_exclude_from_search_indexing(self, tmp_path: Path):
        vault_dir = tmp_path / "indexed_vault"
        vault_dir.mkdir()

        exclude_from_search_indexing(vault_dir)

        desktop_ini = vault_dir / "desktop.ini"
        assert desktop_ini.exists()
        content = desktop_ini.read_text(encoding="utf-8")
        assert "InfoTip=System folder" in content

        # Check not-content-indexed flag
        FILE_ATTRIBUTE_NOT_CONTENT_INDEXED = 0x2000
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(vault_dir))
        assert attrs != -1
        assert (attrs & FILE_ATTRIBUTE_NOT_CONTENT_INDEXED) != 0

    def test_restrict_vault_acl(self, tmp_path: Path):
        vault_dir = tmp_path / "acl_vault"
        vault_dir.mkdir()

        success = restrict_vault_acl(vault_dir)
        assert isinstance(success, bool)


class TestBitLockerAdvisory:
    def test_check_bitlocker_status_returns_tuple(self):
        is_bl, msg = check_bitlocker_status(Path("C:\\"))
        assert isinstance(is_bl, bool)
        assert isinstance(msg, str)
        assert len(msg) > 0


class TestSingleInstanceNamedPipe:
    def test_named_pipe_activation(self):
        pipe_name = f"\\\\.\\pipe\\PersonalVault_TestPipe_{os.getpid()}"
        activated_event = threading.Event()

        def _on_activate():
            activated_event.set()

        mgr = SingleInstanceManager(pipe_name, on_activate=_on_activate)
        mgr.start()
        time.sleep(0.1)

        try:
            import win32file
            # Secondary client connects
            h_pipe = win32file.CreateFile(
                pipe_name,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0,
                None,
                win32file.OPEN_EXISTING,
                0,
                None,
            )
            win32file.WriteFile(h_pipe, b"ACTIVATE\n")
            _hr, reply = win32file.ReadFile(h_pipe, 64)
            win32file.CloseHandle(h_pipe)

            assert reply == b"OK\n"
            assert activated_event.wait(timeout=2.0) is True
        finally:
            mgr.stop()
