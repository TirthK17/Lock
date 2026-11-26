"""U3 Dashboard shell tests: collapsible rail, sliding indicator, view modes, elision, 900x600 fit."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from mylocker.app.core.index import FileEntry
from mylocker.app.ui.components import EmptyState, ErrorState
from mylocker.app.ui.dashboard import Dashboard, FileCard, SidebarWidget
from mylocker.app.ui.design_system import apply_theme
from mylocker.app.ui.file_grid import VaultFileListView


@pytest.fixture(autouse=True)
def _dark_tokens(qtbot):
    os.environ["MYLOCKER_FORCE_REDUCED_MOTION"] = "1"
    app = QApplication.instance()
    if app is not None:
        apply_theme(app, "dark")
    yield
    os.environ.pop("MYLOCKER_FORCE_REDUCED_MOTION", None)


class _DummyIndex:
    def __init__(self, entries: list[FileEntry]) -> None:
        self._entries = list(entries)
        self.index = {e.file_id: e for e in self._entries}

    def all_files(self) -> list[FileEntry]:
        return list(self._entries)


class _DummyVault:
    def __init__(self, vault_dir: Path, count: int = 4) -> None:
        self.vault_dir = vault_dir
        entries = [
            FileEntry(
                file_id=f"id-{i}",
                original_name=f"very_long_confidential_document_name_number_{i:03d}_final_v2.pdf" if i == 0 else f"doc_{i}.txt",
                original_path=str(vault_dir / f"doc_{i}"),
                vault_path=f"files/id-{i}.bin",
                size_bytes=1024 * (i + 1),
                modified_time=1700000000.0 + i,
                mime_type="application/pdf" if i == 0 else "text/plain",
                thumbnail_b64="",
                is_favorite=(i == 1),
            )
            for i in range(count)
        ]
        self._index = _DummyIndex(entries)

    def load(self):
        return self._index

    def refresh(self):
        return self._index

    def save_index(self) -> None:
        pass


class _DummySession:
    is_unlocked = True
    is_decoy = False

    @staticmethod
    def get_master_key() -> bytes:
        return b"\x00" * 32


class _DummyLockController(QObject):
    hotkey_registration_failed = Signal(str)
    is_hotkey_registered = True
    hotkey_error_message = ""

    def register_window(self, _w):
        pass

    def unregister_window(self, _w):
        pass

    def register_pre_lock_hook(self, _cb):
        pass

    def unregister_pre_lock_hook(self, _cb):
        pass

    def start_listeners(self):
        pass

    def set_screenshot_protection(self, _enabled: bool):
        pass


class _DummyWinFilter:
    def register_window(self, _w):
        pass

    def unregister_window(self, _w):
        pass


@contextmanager
def _open_dashboard(qtbot, tmp_path: Path, count: int = 4):
    vault = _DummyVault(tmp_path, count=count)
    lc = _DummyLockController()
    wf = _DummyWinFilter()
    with (
        patch("mylocker.app.system.lock_controller.get_lock_controller", return_value=lc),
        patch("mylocker.app.system.win32_events.install_windows_security_filter", return_value=wf),
        patch("mylocker.app.system.screenshot.apply_screenshot_protection", return_value=True),
    ):
        dash = Dashboard(vault=vault, session=_DummySession(), keystore=None, lockout=None, settings_store=None)
        qtbot.addWidget(dash)
        dash.resize(900, 600)
        dash.show()
        QApplication.processEvents()
        dash.load_files(initial=True)
        QApplication.processEvents()
        try:
            yield dash
        finally:
            dash.cleanup()


def test_sidebar_collapses_232_to_64_and_updates_indicator(qtbot):
    sb = SidebarWidget(is_decoy=False)
    qtbot.addWidget(sb)
    sb.resize(232, 600)
    sb.show()
    QApplication.processEvents()

    assert sb.width() == SidebarWidget.EXPANDED_WIDTH
    assert not sb.is_collapsed()

    sb.set_collapsed(True, animate=False)
    QApplication.processEvents()
    assert sb.is_collapsed()
    assert sb.width() == SidebarWidget.COLLAPSED_WIDTH

    # Switching active item moves floating sliding indicator
    sb.set_active_item(sb.nav_items[2], animate=False)
    QApplication.processEvents()
    assert sb._indicator.isVisible()
    assert sb._indicator.geometry() == sb.nav_items[2].geometry()


def test_dashboard_shell_minimum_size_900x600_and_toolbar_fit(qtbot, tmp_path):
    with _open_dashboard(qtbot, tmp_path, count=4) as dash:
        assert dash.minimumWidth() == 900
        assert dash.minimumHeight() == 600
        assert dash.width() == 900
        assert dash.height() == 600
        assert dash._title_bar.height() == 36
        # Toolbar controls must stay completely inside content area without overlapping or negative geometry
        assert dash._toolbar.width() <= dash._content_widget.width()
        for w in (dash._search, dash._toolbar.view_switch, dash._sort_combo, dash._select_btn, dash._fab_add):
            assert w.width() > 0
            assert w.x() + w.width() <= dash._toolbar.width() + 2
        # Status bar reflects vault file count
        assert "4 files" in dash._status_bar.currentMessage()


def test_dashboard_view_mode_toggle_grid_and_list(qtbot, tmp_path):
    with _open_dashboard(qtbot, tmp_path, count=3) as dash:
        assert dash._scroll.isVisible()
        assert not dash._list_view.isVisible()
        assert len(dash._rendered_cards) == 3

        # Switch to List view
        dash._btn_list_mode.click()
        QApplication.processEvents()
        assert dash._view_mode == "list"
        assert isinstance(dash._list_view, VaultFileListView)
        assert dash._list_view.isVisible()
        assert not dash._scroll.isVisible()
        assert dash._list_view.file_model.rowCount() == 3

        # Switch back to Grid view
        dash._btn_grid_mode.click()
        QApplication.processEvents()
        assert dash._view_mode == "grid"
        assert dash._scroll.isVisible()
        assert not dash._list_view.isVisible()


def test_dashboard_empty_and_error_states(qtbot, tmp_path):
    with _open_dashboard(qtbot, tmp_path, count=0) as dash:
        assert isinstance(dash._drop_hint, EmptyState)
        assert isinstance(dash._error_state, ErrorState)
        assert dash._drop_hint.isVisible()
        assert not dash._scroll.isVisible()

        # Simulate vault load failure displaying ErrorState cleanly
        dash._vault.load = lambda: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore[method-assign]
        dash._vault.refresh = lambda: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore[method-assign]
        dash.load_files(initial=True)
        QApplication.processEvents()
        assert dash._error_state.isVisible()
        assert not dash._drop_hint.isVisible()


def test_file_card_elides_long_name_and_keeps_full_tooltip(qtbot, tmp_path):
    entry = FileEntry(
        file_id="f1",
        original_name="extremely_long_confidential_tax_return_2025_signed_final_copy.pdf",
        original_path=str(tmp_path / "tax.pdf"),
        vault_path="files/f1.bin",
        size_bytes=2048,
        modified_time=1700000000.0,
        mime_type="application/pdf",
        thumbnail_b64="",
        is_favorite=False,
    )
    card = FileCard(entry)
    qtbot.addWidget(card)
    assert card.graphicsEffect() is None
    assert entry.original_name in card.toolTip()
    assert card._name_lbl.text() != entry.original_name
    assert "…" in card._name_lbl.text()
