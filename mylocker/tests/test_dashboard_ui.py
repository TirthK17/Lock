"""
test_dashboard_ui.py — Comprehensive offscreen regression tests for post-login
Dashboard navigation, FileCard clicks, GalleryViewer, StreamingVaultIODevice,
SettingsStore HMAC consistency, AppLockUI persistence, and infer_mime_type.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from PySide6.QtCore import QIODevice, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QLabel

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from mylocker.app.applock.config import AppLockConfig
from mylocker.app.core.kdf import KDFParams
from mylocker.app.core.keystore import Keystore
from mylocker.app.core.lockout import LockoutManager, make_lockout_signing_key
from mylocker.app.core.session import get_session
from mylocker.app.core.settings_store import SettingsStore
from mylocker.app.core.vault import Vault, infer_mime_type
from mylocker.app.ui.applock_ui import AppLockUI
from mylocker.app.ui.dashboard import Dashboard, FileCard
from mylocker.app.ui.gallery import GalleryViewer
from mylocker.app.ui.settings import SettingsUI
from mylocker.app.ui.video_player import StreamingVaultIODevice


def _make_png_bytes(width: int = 32, height: int = 32) -> bytes:
    from PySide6.QtCore import QBuffer
    img = QImage(width, height, QImage.Format.Format_RGBA8888)
    img.fill(0xFF336699)
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


@pytest.fixture
def unlocked_env(tmp_path):
    vault_dir = tmp_path / "DashVault"
    vault_dir.mkdir(parents=True, exist_ok=True)

    keystore = Keystore(vault_dir)
    with patch("mylocker.app.core.keystore.benchmark_argon2id") as mock_bench:
        mock_bench.return_value = KDFParams.new_with_fresh_salt(memory_kib=1024, time_cost=1)
        mk, _ = keystore.setup("StrongVaultPass123#", benchmark=False)

    session = get_session()
    session.unlock(mk, is_decoy=False)

    vault = Vault(vault_dir)
    vault.initialise()

    keystore.config.load()
    signing_key = make_lockout_signing_key(keystore.config.kdf_params.salt)
    lockout = LockoutManager(vault_dir, signing_key)
    settings_store = SettingsStore(vault_dir, signing_key)

    yield vault_dir, vault, session, keystore, lockout, settings_store, signing_key

    session.lock()


class TestMimeInference:
    def test_modern_media_extensions_inferred_accurately(self):
        assert infer_mime_type("pic.webp", "application/octet-stream") == "image/webp"
        assert infer_mime_type("photo.jfif", "") == "image/jpeg"
        assert infer_mime_type("shot.heic", None) == "image/heic"
        assert infer_mime_type("anim.avif", "application/octet-stream") == "image/avif"
        assert infer_mime_type("movie.mkv", "application/octet-stream") == "video/x-matroska"
        assert infer_mime_type("clip.webm", "") == "video/webm"
        assert infer_mime_type("cam.mov", None) == "video/quicktime"
        assert infer_mime_type("film.m4v", "application/octet-stream") == "video/mp4"
        assert infer_mime_type("notes.pdf", None) == "application/pdf"
        assert infer_mime_type("report.docx", None).startswith("application/")


class TestDashboardSidebarAndFiltering:
    def test_sidebar_navigation_filters_all_categories(self, qtbot, unlocked_env, tmp_path):
        vault_dir, vault, session, keystore, lockout, settings_store, _ = unlocked_env

        src_dir = tmp_path / "inputs"
        src_dir.mkdir()
        png_bytes = _make_png_bytes()

        samples = [
            ("photo1.png", png_bytes),
            ("photo2.webp", b"RIFF....WEBP"),
            ("clip1.mp4", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64),
            ("clip2.mkv", b"\x1a\x45\xdf\xa3" + b"\x00" * 64),
            ("doc1.pdf", b"%PDF-1.4\n%test"),
            ("notes.txt", b"hello vault"),
        ]
        entries = []
        for name, data in samples:
            p = src_dir / name
            p.write_bytes(data)
            entries.append(vault.add_file(p))

        # Mark photo1.png as favorite
        entries[0].is_favorite = True
        vault._save_index()

        dash = Dashboard(
            vault=vault,
            session=session,
            keystore=keystore,
            lockout=lockout,
            settings_store=settings_store,
        )
        qtbot.addWidget(dash)
        dash.show()
        dash.load_files(initial=True)

        try:
            assert len(dash._current_entries) == 6
            assert dash._page_title.text() == "All Files"

            # Click Photos (index 1)
            qtbot.mouseClick(dash._nav_items[1], Qt.MouseButton.LeftButton)
            assert dash._current_section == "photos"
            assert dash._page_title.text() == "Photos"
            assert dash._page_stack.currentIndex() == 0
            assert {e.original_name for e in dash._current_entries} == {"photo1.png", "photo2.webp"}

            # Click Videos (index 2)
            qtbot.mouseClick(dash._nav_items[2], Qt.MouseButton.LeftButton)
            assert dash._current_section == "videos"
            assert dash._page_title.text() == "Videos"
            assert dash._page_stack.currentIndex() == 0
            assert {e.original_name for e in dash._current_entries} == {"clip1.mp4", "clip2.mkv"}

            # Click Documents (index 3)
            qtbot.mouseClick(dash._nav_items[3], Qt.MouseButton.LeftButton)
            assert dash._current_section == "documents"
            assert dash._page_title.text() == "Documents"
            assert dash._page_stack.currentIndex() == 0
            assert {e.original_name for e in dash._current_entries} == {"doc1.pdf", "notes.txt"}

            # Click Favorites (index 4)
            qtbot.mouseClick(dash._nav_items[4], Qt.MouseButton.LeftButton)
            assert dash._current_section == "favorites"
            assert dash._page_title.text() == "Favorites"
            assert dash._page_stack.currentIndex() == 0
            assert {e.original_name for e in dash._current_entries} == {"photo1.png"}

            # Click Intruder Log (index 5)
            qtbot.mouseClick(dash._nav_items[5], Qt.MouseButton.LeftButton)
            assert dash._page_title.text() == "Intruder Log"
            assert dash._page_stack.currentIndex() == 1

            # Click App Lock (index 6)
            qtbot.mouseClick(dash._nav_items[6], Qt.MouseButton.LeftButton)
            assert dash._page_title.text() == "App Lock"
            assert dash._page_stack.currentIndex() == 2

            # Click Settings (index 7)
            qtbot.mouseClick(dash._nav_items[7], Qt.MouseButton.LeftButton)
            assert dash._page_title.text() == "Settings"
            assert dash._page_stack.currentIndex() == 3

            # Return to All Files (index 0)
            qtbot.mouseClick(dash._nav_items[0], Qt.MouseButton.LeftButton)
            assert dash._current_section == "all"
            assert dash._page_stack.currentIndex() == 0
            assert len(dash._current_entries) == 6
        finally:
            dash.cleanup()

    def test_file_card_child_click_triggers_open_requested(self, qtbot, unlocked_env, tmp_path):
        _, vault, _, _, _, _, _ = unlocked_env
        img_path = tmp_path / "img.png"
        img_path.write_bytes(_make_png_bytes())
        entry = vault.add_file(img_path)

        card = FileCard(entry)
        qtbot.addWidget(card)
        card.show()

        child_labels = card.findChildren(QLabel)
        assert child_labels, "Expected FileCard child QLabel widgets"

        with qtbot.waitSignal(card.open_requested, timeout=2000) as blocker:
            qtbot.mouseClick(card, Qt.MouseButton.LeftButton)
        assert blocker.args == [entry.file_id]


class TestGalleryAndStreamingVideoDevice:
    def test_gallery_viewer_renders_and_zeros_memory_on_close(self, qtbot, unlocked_env, tmp_path):
        _, vault, _, _, _, _, _ = unlocked_env
        img_bytes = _make_png_bytes(64, 48)
        img_path = tmp_path / "sample.png"
        img_path.write_bytes(img_bytes)
        entry = vault.add_file(img_path)

        buf = bytearray(img_bytes)
        viewer = GalleryViewer(entry.file_id, buf, vault)
        viewer.show()
        qtbot.waitUntil(
            lambda: viewer._img_lbl._original_pixmap is not None
            and not viewer._img_lbl._original_pixmap.isNull(),
            timeout=3000,
        )

        viewer.accept()
        qtbot.wait(50)
        assert all(b == 0 for b in buf)

    def test_streaming_vault_io_device_seek_and_chunk_boundary_read(self, unlocked_env, tmp_path):
        vault_dir, vault, session, _, _, _, _ = unlocked_env
        payload = os.urandom(200 * 1024)  # > 3 chunks (64 KiB each)
        vid_path = tmp_path / "video.mp4"
        vid_path.write_bytes(payload)
        entry = vault.add_file(vid_path)

        mk = session.get_master_key()
        file_id_bytes = bytes.fromhex(entry.file_id)
        blob_path = vault_dir / "blobs" / entry.blob_name

        dev = StreamingVaultIODevice(blob_path, mk, file_id_bytes)
        assert dev.open(QIODevice.OpenModeFlag.ReadOnly)
        try:
            assert dev.size() == len(payload)
            assert dev.read(128) == payload[:128]
            assert dev.seek(0)
            assert dev.pos() == 0
            assert bytes(dev.readAll()) == payload

            # Seek across chunk boundary
            offset = 65536 - 17
            assert dev.seek(offset)
            chunk_cross = bytes(dev.read(1024))
            assert chunk_cross == payload[offset:offset + 1024]
        finally:
            dev.zero_and_close()


class TestSettingsAndAppLockPersistence:
    def test_settings_store_hmac_shared_with_settings_ui(self, qtbot, unlocked_env):
        vault_dir, vault, _, keystore, _, settings_store, signing_key = unlocked_env

        ui = SettingsUI(vault, keystore, settings_store=settings_store)
        qtbot.addWidget(ui)

        ui.idle_spin.setValue(17)
        ui.webcam_check.setChecked(True)
        ui.lock_minimize_check.setChecked(False)

        reloaded = SettingsStore(vault_dir, signing_key)
        assert reloaded.get("idle_timeout_minutes") == 17
        assert reloaded.get("intruder_webcam_enabled") is True
        assert reloaded.get("lock_on_minimize") is False

    def test_applock_ui_persists_scanned_app_and_grace_period(self, qtbot, tmp_path):
        vault_dir = tmp_path / "AppLockVault"
        vault_dir.mkdir(parents=True, exist_ok=True)
        cfg = AppLockConfig(vault_dir)
        ui = AppLockUI(cfg)
        qtbot.addWidget(ui)

        ui.grace_spin.setValue(12)
        assert cfg.get_grace_minutes() == 12

        with patch(
            "mylocker.app.ui.applock_ui.get_running_gui_processes",
            return_value=[{"name": "notepad.exe", "pid": 4242}],
        ), patch(
            "PySide6.QtWidgets.QInputDialog.getItem",
            return_value=("notepad.exe (PID: 4242)", True),
        ):
            ui._scan_running()

        assert "notepad.exe" in cfg.get_locked_apps()
        reloaded = AppLockConfig(vault_dir)
        assert "notepad.exe" in reloaded.get_locked_apps()
        assert reloaded.get_grace_minutes() == 12


class TestMainAppLifecycle:
    def test_personal_vault_app_login_and_lock_cleanup(self, qtbot, unlocked_env, tmp_path):
        from PySide6.QtWidgets import QApplication

        from mylocker.app.system.lock_controller import get_lock_controller
        from mylocker.main import PersonalVaultApp

        vault_dir, _, session, keystore, _, _, _ = unlocked_env
        session.lock()
        # Reset lock controller handlers left over from earlier tests
        lc = get_lock_controller()

        app = QApplication.instance()
        with patch("mylocker.main._load_vault_dir", return_value=vault_dir), \
             patch("mylocker.main.hide_vault_directory"), \
             patch("mylocker.main.exclude_from_search_indexing"), \
             patch("mylocker.main.restrict_vault_acl"):
            vault_app = PersonalVaultApp(app)
            qtbot.addWidget(vault_app.stack)
            try:
                assert vault_app.stack.currentIndex() == 0  # Login screen
                assert vault_app._dashboard is None

                mk = keystore.unlock_with_password("StrongVaultPass123#")
                vault_app._on_login_success(mk, False)
                assert vault_app._dashboard is not None
                assert session.is_unlocked is True

                vault_app.lock_vault("user")
                qtbot.wait(50)
                assert session.is_unlocked is False
                assert vault_app._dashboard is None
                assert vault_app.stack.currentIndex() == 0
            finally:
                vault_app.panic_hotkey.stop()
                if vault_app.applock_supervisor:
                    vault_app.applock_supervisor.stop()
                vault_app.tray.hide()
                try:
                    lc.locked.disconnect(vault_app._on_central_locked)
                except Exception:
                    pass

