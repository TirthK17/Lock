"""U9 Performance tests: >500-file model/view virtualization, LRU caches, pause on hidden/lock."""

from __future__ import annotations

import base64
import os
import time
from collections import OrderedDict
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QAbstractAnimation, QEvent, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QWidget

from mylocker.app.core.index import FileEntry
from mylocker.app.ui import icons
from mylocker.app.ui.dashboard import Dashboard
from mylocker.app.ui.design_system import Tokens, apply_theme
from mylocker.app.ui.file_grid import FileCard
from mylocker.app.ui.login import LoginScreen


@pytest.fixture(autouse=True)
def _u9_env(qtbot):
    os.environ["MYLOCKER_FORCE_REDUCED_MOTION"] = "1"
    app = QApplication.instance()
    if app is not None:
        apply_theme(app, "dark")
    yield
    os.environ.pop("MYLOCKER_FORCE_REDUCED_MOTION", None)
    if app is not None:
        apply_theme(app, "dark")


def _tiny_png_b64() -> str:
    img = QImage(4, 4, QImage.Format.Format_RGBA8888)
    img.fill(Qt.GlobalColor.white)
    from PySide6.QtCore import QBuffer, QIODevice
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return base64.b64encode(bytes(buf.data())).decode("ascii")


def _make_mocks(tmp_path: Path):
    ks = MagicMock()
    ks.vault_exists.return_value = True
    ks.has_keyfile.return_value = False

    lm = MagicMock()
    lm.is_locked_out.return_value = False
    lm.remaining_seconds.return_value = 0
    lm.get_lockout_seconds.return_value = 0

    ss = MagicMock()
    ss.get.side_effect = lambda _k, default=None: default

    vault = MagicMock()
    vault.is_unlocked = True
    vault.vault_dir = tmp_path
    vault._index.index.all_files.return_value = []
    vault.list_entries.return_value = []
    vault.list_trash.return_value = []
    vault.get_total_size.return_value = 0

    session = MagicMock()
    session.is_unlocked = True
    session.key = b"\x00" * 32
    return ks, lm, ss, vault, session


def test_over_500_files_uses_virtualized_list_view_without_spawning_cards(qtbot, tmp_path):
    ks, lm, ss, vault, session = _make_mocks(tmp_path)
    dash = Dashboard(vault=vault, session=session, keystore=ks, lockout=lm, settings_store=ss)
    qtbot.addWidget(dash)
    dash.resize(960, 640)
    dash.show()

    entries = [
        FileEntry(
            file_id=f"id_{i:04d}",
            original_name=f"document_{i:04d}.pdf",
            original_path=f"C:/tmp/document_{i:04d}.pdf",
            vault_path="",
            size_bytes=1024 + i,
            modified_time=1700000000.0 + i,
            mime_type="application/pdf",
        )
        for i in range(600)
    ]

    t0 = time.perf_counter()
    dash._populate_grid(entries)
    QApplication.processEvents()
    elapsed = time.perf_counter() - t0

    assert dash._use_virtual_list_view() is True
    assert dash._list_view.isVisible() is True
    assert dash._list_view.file_model.rowCount() == 600
    assert len(dash._rendered_cards) == 0
    assert elapsed < 1.5
    dash.close()


def test_thumbnail_and_icon_lru_caches_evict_oldest_while_preserving_hot(qtbot):
    thumb_b64 = _tiny_png_b64()
    cache: OrderedDict = OrderedDict()

    # Fill to capacity (512 entries)
    hot_entry = FileEntry(
        file_id="hot_0",
        original_name="hot_0.png",
        original_path="C:/tmp/hot_0.png",
        vault_path="",
        size_bytes=64,
        modified_time=10.0,
        mime_type="image/png",
        thumbnail_b64=thumb_b64,
    )
    hot_card = FileCard(hot_entry, thumb_cache=cache)
    qtbot.addWidget(hot_card)

    for i in range(1, 512):
        e = FileEntry(
            file_id=f"fill_{i}",
            original_name=f"fill_{i}.png",
            original_path=f"C:/tmp/fill_{i}.png",
            vault_path="",
            size_bytes=64,
            modified_time=10.0 + i,
            mime_type="image/png",
            thumbnail_b64=thumb_b64,
        )
        c = FileCard(e, thumb_cache=cache)
        qtbot.addWidget(c)

    assert len(cache) == 512
    hot_key = ("hot_0", 10.0, len(thumb_b64))
    assert hot_key in cache

    # Re-access hot_0 so LRU moves it to end; then add 2 new items
    hot_hit = FileCard(hot_entry, thumb_cache=cache)
    qtbot.addWidget(hot_hit)

    for j in range(2):
        extra = FileEntry(
            file_id=f"extra_{j}",
            original_name=f"extra_{j}.png",
            original_path=f"C:/tmp/extra_{j}.png",
            vault_path="",
            size_bytes=64,
            modified_time=999.0 + j,
            mime_type="image/png",
            thumbnail_b64=thumb_b64,
        )
        c = FileCard(extra, thumb_cache=cache)
        qtbot.addWidget(c)

    assert len(cache) == 512
    # Oldest untouched (fill_1, fill_2) evicted while hot_0 survived
    assert ("fill_1", 11.0, len(thumb_b64)) not in cache
    assert ("fill_2", 12.0, len(thumb_b64)) not in cache
    assert hot_key in cache

    # Check icon pixmap LRU preserves cap <= MAX_ICON_CACHE without mass clear
    icons.clear_icon_cache()
    for i in range(icons.MAX_ICON_CACHE + 25):
        icons.get_pixmap("file", color=f"#{i:06x}", size=20, dpr=1.0)
    assert len(icons._PIXMAP_CACHE) == icons.MAX_ICON_CACHE


def test_animations_and_timers_pause_when_hidden_or_locked(qtbot, tmp_path):
    os.environ.pop("MYLOCKER_FORCE_REDUCED_MOTION", None)
    ks, lm, ss, vault, session = _make_mocks(tmp_path)

    with patch("mylocker.app.ui.motion.is_reduced_motion", return_value=False):
        login = LoginScreen(keystore=ks, lockout_manager=lm, settings_store=ss)
        qtbot.addWidget(login)
        login.show()
        QApplication.processEvents()
        assert login._backdrop._drift_timer.isActive() is True

        login.hide()
        QApplication.processEvents()
        assert login._backdrop._drift_timer.isActive() is False
        assert login._status_badge._pulse_timer.isActive() is False
        login.close()

        dash = Dashboard(vault=vault, session=session, keystore=ks, lockout=lm, settings_store=ss)
        qtbot.addWidget(dash)
        dash.show()
        dash._loading_spinner.start()
        dash.hide()
        QApplication.processEvents()
        assert dash._loading_spinner._timer.isActive() is False
        assert all(
            anim.state() != QAbstractAnimation.State.Running
            for anim in dash.findChildren(QAbstractAnimation)
        )
        dash._on_pre_lock()
        assert dash._grid_render_timer.isActive() is False
        assert dash._resize_timer.isActive() is False
        dash.close()


def test_design_tokens_prunes_dead_qt_theme_listeners(qtbot):
    w = QWidget()
    called = []

    def _on_theme(pal):
        called.append(pal.name)

    w._cb = _on_theme  # type: ignore[attr-defined]
    # Subscribe a bound method on QWidget that will be deleted
    w_holder = type("_Holder", (QWidget,), {"on_t": lambda self, pal: called.append(pal.name)})()
    cb = w_holder.on_t
    Tokens.subscribe(cb)
    assert cb in Tokens._listeners

    w_holder.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()

    Tokens.set_theme("dark")
    assert cb not in Tokens._listeners
    assert called == []
    w.deleteLater()
