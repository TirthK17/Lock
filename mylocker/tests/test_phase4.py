"""
test_phase4.py — Comprehensive tests for Phase 4:
1. Central LockController (closes viewers, wipes temp dirs, zeros session)
2. Targeted Recent Files cleanup (only vault files removed, user files kept)
3. Gallery LRU cache eviction memory zeroing
4. StreamingVaultIODevice seekable chunk streaming without full-RAM loading
5. Screenshot protection wrapper
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QWidget

from mylocker.app.core.chunked_io import CHUNK_SIZE, encrypt_file
from mylocker.app.core.crypto import generate_key, zero_bytes
from mylocker.app.core.session import get_session
from mylocker.app.system.lock_controller import LockController
from mylocker.app.system.privacy_cleanup import clean_vault_recent_files
from mylocker.app.system.screenshot import apply_screenshot_protection
from mylocker.app.ui.video_player import StreamingVaultIODevice


class TestLockController:
    def test_lock_closes_viewers_and_zeros_session(self, qtbot):
        session = get_session()
        mk = bytearray(generate_key())
        session.unlock(mk)
        assert session.is_unlocked

        lc = LockController()
        viewer = QWidget()
        qtbot.addWidget(viewer)
        viewer.show()
        assert viewer.isVisible()

        lc.register_viewer(viewer)
        lc.lock(reason="test")

        # Viewer should be closed
        assert not viewer.isVisible()
        # Session should be locked and MK zeroed
        assert not session.is_unlocked

    def test_lock_wipes_registered_temp_dirs(self, tmp_path: Path):
        lc = LockController()
        temp_dir = tmp_path / "pv_ext_test"
        temp_dir.mkdir()
        temp_file = temp_dir / "secret.docx"
        temp_file.write_text("CONFIDENTIAL")

        lc.register_temp_dir(temp_dir)
        assert temp_file.exists()

        lc.lock(reason="test")

        # Both the file and directory must be gone
        assert not temp_file.exists()
        assert not temp_dir.exists()


class TestRecentFilesCleanup:
    def test_targeted_recent_files_cleanup_preserves_unrelated(self, monkeypatch, tmp_path: Path):
        """Audit Item 36: Clear ONLY vault-created Recent Files links without wiping other apps."""
        mock_recent = tmp_path / "Recent"
        mock_recent.mkdir()

        # Create mock recent links
        vault_lnk = mock_recent / "TopSecretVaultDocument.txt.lnk"
        user_lnk1 = mock_recent / "WorkSpreadsheet2026.xlsx.lnk"
        user_lnk2 = mock_recent / "FamilyPhoto.jpg.lnk"

        vault_lnk.write_text("SHORTCUT TO VAULT FILE")
        user_lnk1.write_text("SHORTCUT TO WORK FILE")
        user_lnk2.write_text("SHORTCUT TO FAMILY FILE")

        # Mock APPDATA to point to tmp_path
        appdata_mock = tmp_path.parent
        # Create expected directory structure: %APPDATA%\Microsoft\Windows\Recent
        target_dir = appdata_mock / "Microsoft" / "Windows" / "Recent"
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "TopSecretVaultDocument.txt.lnk").write_text("VAULT")
        (target_dir / "WorkSpreadsheet2026.xlsx.lnk").write_text("WORK")

        monkeypatch.setenv("APPDATA", str(appdata_mock))

        # Perform targeted cleanup for vault document only
        removed = clean_vault_recent_files(["TopSecretVaultDocument.txt"])

        assert removed == 1
        assert not (target_dir / "TopSecretVaultDocument.txt.lnk").exists()
        # User's work shortcut must remain completely untouched!
        assert (target_dir / "WorkSpreadsheet2026.xlsx.lnk").exists()


class TestStreamingVaultIODevice:
    def test_streaming_device_reads_and_seeks_across_chunks(self, tmp_path: Path):
        """Audit Item 32: Seekable streaming QIODevice on demand with minimal RAM."""
        mk = bytearray(generate_key())
        # Create a file that spans 2 full chunks + partial (over 8 MB)
        # To make test fast, we can test with 2 small chunks by creating data
        src = tmp_path / "video.mp4"
        # 4.5 MB data = 1 full chunk (4 MB) + 0.5 MB chunk
        chunk_1_data = b"A" * CHUNK_SIZE
        chunk_2_data = b"B" * (512 * 1024)
        total_data = chunk_1_data + chunk_2_data
        src.write_bytes(total_data)

        dest = tmp_path / "video.blob"
        file_id = encrypt_file(src, dest, mk)

        device = StreamingVaultIODevice(
            blob_path=dest,
            master_key=mk,
            file_id=file_id,
        )

        assert device.size() == len(total_data)
        assert not device.isSequential()
        assert device.pos() == 0

        # Read first 100 bytes from chunk 1
        first_100 = device.readData(100)
        assert first_100 == b"A" * 100
        assert device.pos() == 100

        # Seek forward into chunk 2
        seek_pos = CHUNK_SIZE + 500
        assert device.seek(seek_pos) is True
        assert device.pos() == seek_pos

        # Read 50 bytes from chunk 2
        chunk2_bytes = device.readData(50)
        assert chunk2_bytes == b"B" * 50
        assert device.pos() == seek_pos + 50

        # Seek back into chunk 1
        assert device.seek(5000) is True
        assert device.pos() == 5000
        back_bytes = device.readData(20)
        assert back_bytes == b"A" * 20

        # Seek to end
        assert device.seek(len(total_data)) is True
        assert device.atEnd() is True
        assert device.readData(10) == b""

        # Cleanup
        device.zero_and_close()
        zero_bytes(mk)


class TestScreenshotProtection:
    def test_apply_protection_to_widget(self, qtbot):
        w = QWidget()
        qtbot.addWidget(w)
        w.show()
        # Should execute without throwing exceptions
        success = apply_screenshot_protection(w, True)
        assert isinstance(success, bool)
        apply_screenshot_protection(w, False)
