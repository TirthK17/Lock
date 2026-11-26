"""U6 Complete Empty, Loading (Skeleton / Spinner), and Error States tests."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication

from mylocker.app.core.index import FileEntry
from mylocker.app.ui.applock_ui import AppLockUI
from mylocker.app.ui.components import ErrorState, SkeletonRow, Spinner
from mylocker.app.ui.dashboard import Dashboard
from mylocker.app.ui.design_system import apply_theme
from mylocker.app.ui.gallery import GalleryViewer
from mylocker.app.ui.intruder_log import IntruderLogUI


@pytest.fixture(autouse=True)
def _dark_tokens(qtbot):
    os.environ["MYLOCKER_FORCE_REDUCED_MOTION"] = "1"
    app = QApplication.instance()
    if app is not None:
        apply_theme(app, "dark")
    yield
    os.environ.pop("MYLOCKER_FORCE_REDUCED_MOTION", None)


class _DummyIndex:
    index: dict = {}

    def all_files(self):
        return []


class _DummyVault:
    def __init__(self, vault_dir: Path) -> None:
        self.vault_dir = vault_dir
        self._index = _DummyIndex()

    def load(self):
        return self._index

    def refresh(self):
        return self._index


class _DummySession:
    is_unlocked = True
    is_decoy = False

    @staticmethod
    def get_master_key() -> bytes:
        return b"\x00" * 32


def test_dashboard_loading_skeleton_empty_and_error_states(qtbot, tmp_path):
    with (
        patch("mylocker.app.system.lock_controller.get_lock_controller"),
        patch("mylocker.app.system.win32_events.install_windows_security_filter"),
        patch("mylocker.app.system.screenshot.apply_screenshot_protection", return_value=True),
    ):
        dash = Dashboard(vault=_DummyVault(tmp_path), session=_DummySession(), keystore=None, lockout=None, settings_store=None)
        qtbot.addWidget(dash)
        dash.show()
        QApplication.processEvents()
        try:
            assert isinstance(dash._loading_spinner, Spinner)
            skeletons = dash._loading_box.findChildren(SkeletonRow)
            assert len(skeletons) == 4

            dash._set_loading_state(True)
            assert dash._loading_box.isVisible()
            assert not dash._drop_hint.isVisible()
            assert not dash._scroll.isVisible()

            dash._set_loading_state(False)
            assert not dash._loading_box.isVisible()
        finally:
            dash.cleanup()


def test_intruder_log_error_state_on_read_failure(qtbot, tmp_path):
    class _BrokenLockout:
        vault_dir = tmp_path

        def get_intruder_log(self):
            raise RuntimeError("corrupt log")

    ui = IntruderLogUI(_BrokenLockout())
    qtbot.addWidget(ui)
    ui.show()
    QApplication.processEvents()
    assert isinstance(ui._error_state, ErrorState)
    assert ui._error_state.isVisible()
    assert not ui.table.isVisible()
    assert not ui._empty_state.isVisible()


def test_applock_loading_and_error_states(qtbot):
    with (
        patch("mylocker.app.ui.applock_ui.is_run_at_startup_enabled", return_value=False),
        patch("mylocker.app.ui.applock_ui.scan_installed_applications", side_effect=RuntimeError("scan failed")),
    ):
        ui = AppLockUI(set())
        qtbot.addWidget(ui)
        ui.show()
        QApplication.processEvents()

        ui._scan_installed()
        assert ui._loading_bar.isVisible()
        qtbot.waitUntil(lambda: ui._error_state.isVisible(), timeout=5000)
        assert not ui._loading_bar.isVisible()
        ui.cleanup()


def test_gallery_viewer_decode_error_shows_error_state(qtbot):
    bad_bytes = bytearray(b"not-an-image")
    entry = FileEntry(
        file_id="bad1",
        original_name="broken.png",
        original_path="broken.png",
        vault_path="files/bad1.bin",
        size_bytes=len(bad_bytes),
        modified_time=1700000000.0,
        mime_type="image/png",
        thumbnail_b64="",
    )

    class _Idx:
        def all_files(self):
            return [entry]

        def get(self, fid):
            return entry if fid == "bad1" else None

    class _VIdx:
        index = _Idx()

    class _Vault:
        _index = _VIdx()

    with patch("mylocker.app.ui.gallery.apply_screenshot_protection", return_value=True):
        viewer = GalleryViewer("bad1", bad_bytes, _Vault())
        qtbot.addWidget(viewer)
        viewer.show()
        QApplication.processEvents()
        assert viewer._error_state.isVisible()
        assert not viewer._img_scroll.isVisible()
        viewer._cleanup()
