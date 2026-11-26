"""U4 Screen-by-screen consistency tests across Login, Wizard, Settings, IntruderLog, AppLock, Gallery, Calculator."""

from __future__ import annotations

import io
import os
from unittest.mock import patch

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from mylocker.app.core.index import FileEntry
from mylocker.app.ui.applock_ui import AppLockUI
from mylocker.app.ui.calculator_disguise import CalculatorDisguise
from mylocker.app.ui.components import Badge, DialogShell, EmptyState, Toggle
from mylocker.app.ui.design_system import Tokens, apply_theme
from mylocker.app.ui.gallery import GalleryViewer
from mylocker.app.ui.intruder_log import IntruderLogUI
from mylocker.app.ui.login import ForgotPasswordDialog, LoginScreen, RecoveryKeyDialog
from mylocker.app.ui.settings import SettingsUI
from mylocker.app.ui.setup_wizard import SetupWizard


@pytest.fixture(autouse=True)
def _setup_dark_tokens(qtbot):
    os.environ["MYLOCKER_FORCE_REDUCED_MOTION"] = "1"
    app = QApplication.instance()
    if app is not None:
        apply_theme(app, "dark")
    yield
    os.environ.pop("MYLOCKER_FORCE_REDUCED_MOTION", None)


class _DummyLockout:
    def __init__(self, vault_dir, entries=None):
        self.vault_dir = vault_dir
        self._entries = entries or []

    def get_intruder_log(self):
        return list(self._entries)

    def clear_intruder_log(self):
        self._entries.clear()


def test_intruder_log_empty_and_populated_states(qtbot, tmp_path):
    lm = _DummyLockout(tmp_path, entries=[])
    ui = IntruderLogUI(lm)
    qtbot.addWidget(ui)
    ui.show()
    QApplication.processEvents()
    assert isinstance(ui._empty_state, EmptyState)
    assert ui._empty_state.isVisible()
    assert not ui.table.isVisible()

    lm._entries = [{"timestamp": "2026-11-26 12:00:00", "fail_count": 3, "has_photo": False}]
    ui.refresh_log()
    QApplication.processEvents()
    assert not ui._empty_state.isVisible()
    assert ui.table.isVisible()
    assert ui.table.rowCount() == 1
    assert isinstance(ui.table.cellWidget(0, 1), Badge)


def test_calculator_disguise_is_unbranded_and_calculates(qtbot):
    calc = CalculatorDisguise(secret_code="4242")
    qtbot.addWidget(calc)
    # Neutral gray palette — should not contain Personal Vault accent hex
    assert Tokens.colors.accent.lower() not in calc.styleSheet().lower()
    seen = []
    calc.secret_entered.connect(lambda: seen.append(True))

    for ch in ("2", "+", "3", "="):
        calc._on_btn_click(ch)
    assert calc.display.text() == "5"
    assert not seen

    calc._on_btn_click("C")
    for ch in ("4", "2", "4", "2", "="):
        calc._on_btn_click(ch)
    assert seen == [True]


def test_applock_ui_empty_state_and_toggles(qtbot):
    with patch("mylocker.app.ui.applock_ui.is_run_at_startup_enabled", return_value=False):
        ui = AppLockUI(set())
        qtbot.addWidget(ui)
        ui.show()
        QApplication.processEvents()
        assert isinstance(ui.startup_check, Toggle)
        assert isinstance(ui.status_badge, Badge)
        assert ui._empty_state.isVisible()
        assert not ui.table.isVisible()

        ui.protected_exes.add("notepad.exe")
        ui._load_table()
        QApplication.processEvents()
        assert ui.table.isVisible()
        assert not ui._empty_state.isVisible()
        assert ui.table.rowCount() == 1
        ui.cleanup()


def test_settings_ui_has_category_list_toggles_and_bottom_danger_zone(qtbot, tmp_path):
    class _Cfg:
        def exists(self):
            return False

    class _KS:
        config = _Cfg()

    class _Vault:
        vault_dir = tmp_path

    ui = SettingsUI(_Vault(), _KS(), settings_store=None)
    qtbot.addWidget(ui)
    assert ui._cat_list.count() == 7
    assert any(ui._cat_list.item(i).text() == "Appearance" for i in range(ui._cat_list.count()))
    assert ui._cat_list.item(ui._cat_list.count() - 1).text() == "Danger Zone"
    assert isinstance(ui.lock_minimize_check, Toggle)
    assert isinstance(ui.screen_protect_check, Toggle)
    assert isinstance(ui.destruct_check, Toggle)


def test_login_and_wizard_use_400px_card_and_dialog_shell(qtbot, tmp_path):
    class _Cfg:
        def exists(self):
            return False

    class _KS:
        config = _Cfg()

    login = LoginScreen(keystore=_KS())
    qtbot.addWidget(login)
    login.resize(1280, 800)
    login.show()
    QApplication.processEvents()
    assert login._card.width() == 400

    rec_dlg = RecoveryKeyDialog(login)
    qtbot.addWidget(rec_dlg)
    assert isinstance(rec_dlg, DialogShell)

    fp_dlg = ForgotPasswordDialog(_KS(), parent=login)
    qtbot.addWidget(fp_dlg)
    assert isinstance(fp_dlg, DialogShell)

    with patch("mylocker.app.system.bitlocker.check_bitlocker_status", return_value=(False, "Info")):
        wiz = SetupWizard(_KS(), tmp_path)
        qtbot.addWidget(wiz)
        assert len(wiz._dots) == 5
        assert wiz._stack.count() == 6


def test_gallery_viewer_rotate_and_auto_hide_bars(qtbot):
    buf = io.BytesIO()
    Image.new("RGB", (80, 40), (90, 120, 200)).save(buf, format="PNG")
    raw = bytearray(buf.getvalue())

    entry = FileEntry(
        file_id="img1",
        original_name="sample.png",
        original_path="sample.png",
        vault_path="files/img1.bin",
        size_bytes=len(raw),
        modified_time=1700000000.0,
        mime_type="image/png",
        thumbnail_b64="",
    )

    class _Idx:
        def all_files(self):
            return [entry]

        def get(self, fid):
            return entry if fid == "img1" else None

    class _VIdx:
        index = _Idx()

    class _Vault:
        _index = _VIdx()

    with patch("mylocker.app.ui.gallery.apply_screenshot_protection", return_value=True):
        viewer = GalleryViewer("img1", raw, _Vault())
        qtbot.addWidget(viewer)
        viewer.show()
        QApplication.processEvents()
        orig_w = viewer._img_lbl._original_pixmap.width()
        orig_h = viewer._img_lbl._original_pixmap.height()
        viewer._rotate_90()
        assert viewer._img_lbl._original_pixmap.width() == orig_h
        assert viewer._img_lbl._original_pixmap.height() == orig_w
        viewer._hide_overlay_bars()
        assert not viewer._top_bar.isVisible()
        viewer._wake_overlay_bars()
        assert viewer._top_bar.isVisible()
        viewer._cleanup()
