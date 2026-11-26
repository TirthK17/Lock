"""U8 Accessibility tests: 2px focus rings, accessibleName on icon buttons, tab order, contrast, and shortcuts."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QShortcut
from PySide6.QtWidgets import QAbstractButton, QApplication

from mylocker.app.ui.components import SearchBar, Toggle
from mylocker.app.ui.dashboard import Dashboard
from mylocker.app.ui.design_system import (
    DARK_PALETTE,
    LIGHT_PALETTE,
    apply_theme,
    build_global_qss,
)
from mylocker.app.ui.gallery import GalleryViewer
from mylocker.app.ui.login import LoginScreen
from mylocker.app.ui.setup_wizard import SetupWizard
from mylocker.app.ui.video_player import _GlassIconButton


@pytest.fixture(autouse=True)
def _a11y_env(qtbot):
    os.environ["MYLOCKER_FORCE_REDUCED_MOTION"] = "1"
    app = QApplication.instance()
    if app is not None:
        apply_theme(app, "dark")
    yield
    os.environ.pop("MYLOCKER_FORCE_REDUCED_MOTION", None)
    if app is not None:
        apply_theme(app, "dark")


def _luminance(hx: str) -> float:
    h = hx.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))

    def _lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def _contrast_ratio(fg: str, bg: str) -> float:
    l1, l2 = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def test_contrast_meets_wcag_aa_dark_and_light():
    # Dark theme readable text & semantic indicators >= 4.5:1
    for fg, bg in [
        (DARK_PALETTE.text, DARK_PALETTE.bg),
        (DARK_PALETTE.text, DARK_PALETTE.card),
        (DARK_PALETTE.text_secondary, DARK_PALETTE.bg),
        (DARK_PALETTE.text_secondary, DARK_PALETTE.card),
        (DARK_PALETTE.on_accent, DARK_PALETTE.accent),
        (DARK_PALETTE.accent, DARK_PALETTE.card),
        (DARK_PALETTE.success, DARK_PALETTE.card),
        (DARK_PALETTE.warning, DARK_PALETTE.card),
        (DARK_PALETTE.danger, DARK_PALETTE.card),
    ]:
        assert _contrast_ratio(fg, bg) >= 4.5, f"Low dark contrast: {fg} on {bg}"

    # Light theme readable text & semantic indicators >= 4.5:1
    for fg, bg in [
        (LIGHT_PALETTE.text, LIGHT_PALETTE.bg),
        (LIGHT_PALETTE.text, LIGHT_PALETTE.card),
        (LIGHT_PALETTE.on_accent, LIGHT_PALETTE.accent),
        (LIGHT_PALETTE.accent, LIGHT_PALETTE.card),
        (LIGHT_PALETTE.success, LIGHT_PALETTE.card),
        (LIGHT_PALETTE.warning, LIGHT_PALETTE.card),
        (LIGHT_PALETTE.danger, LIGHT_PALETTE.card),
        (LIGHT_PALETTE.on_danger, LIGHT_PALETTE.danger),
    ]:
        assert _contrast_ratio(fg, bg) >= 4.5, f"Low light contrast: {fg} on {bg}"


def test_focus_rings_present_in_qss_and_search_widgets(qtbot):
    for pal in (DARK_PALETTE, LIGHT_PALETTE):
        qss = build_global_qss(pal)
        assert f"border: 2px solid {pal.accent};" in qss
        assert 'QPushButton[variant="danger"]:focus' in qss
        assert 'QPushButton[variant="icon"]:focus' in qss
        assert "QLineEdit:focus" in qss
        assert "QComboBox:focus" in qss
        assert "QSpinBox:focus" in qss
        assert "QCheckBox:focus::indicator" in qss
        assert "QRadioButton:focus::indicator" in qss
        assert "QListWidget:focus" in qss

    sb = SearchBar()
    qtbot.addWidget(sb)
    sb.show()
    sb.setFocus()
    QApplication.processEvents()
    assert sb.hasFocus()
    assert sb.property("focused") == "true"

    tog = Toggle(checked=True)
    qtbot.addWidget(tog)
    tog.show()
    tog.setFocus()
    QApplication.processEvents()
    # Repaint with focus should succeed without error
    tog.grab()


def _make_mocks(tmp_path: Path):
    ks = MagicMock()
    ks.vault_exists.return_value = True
    ks.has_keyfile.return_value = False

    lm = MagicMock()
    lm.is_locked_out.return_value = False
    lm.remaining_seconds.return_value = 0
    lm.get_lockout_seconds.return_value = 0
    lm.get_intruder_log.return_value = []

    ss = MagicMock()
    ss.get.side_effect = lambda _k, default=None: default

    vault = MagicMock()
    vault.is_unlocked = True
    vault.vault_dir = tmp_path
    vault._index.index.all_files.return_value = []
    vault._index.index.get.return_value = None
    vault.list_entries.return_value = []
    vault.list_trash.return_value = []
    vault.get_total_size.return_value = 0

    session = MagicMock()
    session.is_unlocked = True
    session.key = b"\x00" * 32
    return ks, lm, ss, vault, session


def _next_tab_target(widget):
    cur = widget.nextInFocusChain()
    for _ in range(40):
        if cur is None or cur is widget:
            return None
        if cur.focusPolicy() & Qt.FocusPolicy.TabFocus:
            return cur
        cur = cur.nextInFocusChain()
    return None


def test_icon_only_buttons_have_accessible_names_and_logical_tab_order(qtbot, tmp_path):
    ks, lm, ss, vault, session = _make_mocks(tmp_path)

    # Check VideoPlayer _GlassIconButton accessibleName automatic population
    glass = _GlassIconButton("volume_2", tooltip="Mute (M)")
    qtbot.addWidget(glass)
    assert glass.accessibleName() == "Mute (M)"

    login = LoginScreen(keystore=ks, lockout_manager=lm, settings_store=ss)
    qtbot.addWidget(login)
    login.show()
    QApplication.processEvents()
    for btn in login.findChildren(QAbstractButton):
        if btn.isVisible() and not btn.text().strip():
            assert btn.accessibleName(), f"Icon button missing accessibleName on LoginScreen: {btn.objectName()}"
    # Check tab chain progression on LoginScreen
    assert login._pw_field.focusProxy() is login._pw_input
    assert login._pw_input.nextInFocusChain() is login._kf_widget.browse_btn
    login.close()

    wiz = SetupWizard(ks, str(tmp_path))
    qtbot.addWidget(wiz)
    assert _next_tab_target(wiz._loc_edit) is not None
    assert _next_tab_target(wiz._pw1) is wiz._pw2
    assert _next_tab_target(wiz._verify_edits[0]) is wiz._verify_edits[1]
    assert _next_tab_target(wiz._verify_edits[1]) is wiz._verify_edits[2]
    wiz.close()

    dash = Dashboard(vault=vault, session=session, keystore=ks, lockout=lm, settings_store=ss)
    qtbot.addWidget(dash)
    dash.show()
    QApplication.processEvents()
    for btn in dash.findChildren(QAbstractButton):
        if not btn.text().strip():
            assert btn.accessibleName(), f"Icon button missing accessibleName on Dashboard: {btn.objectName()}"
    # Toolbar search input -> Grid toggle -> List toggle tab order
    assert _next_tab_target(dash._toolbar.search_bar._input) is dash._toolbar.btn_grid_mode
    assert _next_tab_target(dash._toolbar.btn_grid_mode) is dash._toolbar.btn_list_mode
    dash.close()

    gal = GalleryViewer("img1", bytearray(), vault)
    qtbot.addWidget(gal)
    gal.show()
    QApplication.processEvents()
    for btn in gal.findChildren(QAbstractButton):
        if not btn.text().strip():
            assert btn.accessibleName(), f"Icon button missing accessibleName on GalleryViewer: {btn.objectName()}"
    gal.close()


def test_dashboard_and_gallery_shortcuts_intact(qtbot, tmp_path):
    ks, lm, ss, vault, session = _make_mocks(tmp_path)

    dash = Dashboard(vault=vault, session=session, keystore=ks, lockout=lm, settings_store=ss)
    qtbot.addWidget(dash)
    dash_keys = {sc.key().toString() for sc in dash.findChildren(QShortcut)}
    for expected in ("Ctrl+Alt+L", "Ctrl+F", "Ctrl+A", "Del", "Esc"):
        assert expected in dash_keys, f"Missing dashboard shortcut: {expected} in {dash_keys}"

    dash.show()
    dash._focus_search()
    QApplication.processEvents()
    assert dash._search.hasFocus()
    assert dash._search.property("focused") == "true"
    dash.close()

    gal = GalleryViewer("img1", bytearray(), vault)
    qtbot.addWidget(gal)
    gal_keys = {sc.key().toString() for sc in gal.findChildren(QShortcut)}
    for expected in ("Left", "Right", "Space", "Esc"):
        assert expected in gal_keys, f"Missing gallery shortcut: {expected} in {gal_keys}"
    gal.close()
